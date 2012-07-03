# This file is part of Rubber and thus covered by the GPL
# (c) Emmanuel Beffara, 2002--2006
"""
BibTeX support for Rubber

This module is a special one: it is triggered by the macros \\bibliography and
\\bibliographystyle and not as a package, so the main system knows about it.
The module provides the following commands:

  path <dir> = adds <dir> to the search path for databases
  stylepath <dir> = adds <dir> to the search path for styles
"""

# Stop python 2.2 from calling "yield" statements syntax errors.
from __future__ import generators

import os, sys
from os.path import *
import re, string

from rubber import _
from rubber import *

re_bibdata = re.compile(r"\\bibdata{(?P<data>.*)}")
re_citation = re.compile(r"\\citation{(?P<cite>.*)}")
re_undef = re.compile("LaTeX Warning: Citation `(?P<cite>.*)' .*undefined.*")

# The regular expression that identifies errors in BibTeX log files is heavily
# heuristic. The remark is that all error messages end with a text of the form
# "---line xxx of file yyy" or "---while reading file zzz". The actual error
# is either the text before the dashes or the text on the previous line.

re_error = re.compile(
	"---(line (?P<line>[0-9]+) of|while reading) file (?P<file>.*)")

class Module (rubber.rules.latex.Module):
	"""
	This class is the module that handles BibTeX in Rubber. It provides the
	funcionality required when compiling documents as well as material to
	parse blg files for diagnostics.
	"""
	def __init__ (self, doc, dict, base=None):
		"""
		Initialize the state of the module and register appropriate functions
		in the main process. The extra arugment 'base' can be used to specify
		the base name of the aux file, it defaults to the document name.
		"""
		self.doc = doc
		self.env = doc.env

		if base is None:
			self.base = doc.src_base
		else:
			self.base = base

		cwd = self.env.vars["cwd"]
		self.bib_path = [cwd]
		if doc.src_path != cwd:
			self.bib_path.append(doc.src_path)
		self.bst_path = [cwd]

		self.undef_cites = None
		self.used_cites = None
		self.style = None
		self.set_style("plain")
		self.db = {}
		self.sorted = 1
		self.run_needed = 0

	#
	# The following method are used to specify the various datafiles that
	# BibTeX uses.
	#

	def do_path (self, path):
		self.bib_path.append(self.doc.abspath(path))

	def do_stylepath (self, path):
		self.bst_path.append(self.doc.abspath(path))

	def do_sorted (self, mode):
		self.sorted = mode in ("true", "yes", "1")

	def add_db (self, name):
		"""
		Register a bibliography database file.
		"""
		for dir in self.bib_path:
			bib = join(dir, name + ".bib")
			if exists(bib):
				self.db[name] = bib
				self.doc.sources[bib] = DependLeaf(self.env, bib)
				self.doc.not_included.append(bib)
				return

	def set_style (self, style):
		"""
		Define the bibliography style used. This method is called when
		\\bibliographystyle is found. If the style file is found in the
		current directory, it is considered a dependency.
		"""
		if self.style:
			old_bst = self.style + ".bst"
			if exists(old_bst) and self.doc.sources.has_key(old_bst):
				del self.doc.sources[old_bst]

		self.style = style
		for dir in self.bst_path:
			new_bst = join(dir, style + ".bst")
			if exists(new_bst):
				self.bst_file = new_bst
				self.doc.sources[new_bst] = DependLeaf(self.env, new_bst)
				return
		self.bst_file = None

	#
	# The following methods are responsible of detecting when running BibTeX
	# is needed and actually running it.
	#

	def pre_compile (self):
		"""
		Run BibTeX if needed before the first compilation. This function also
		checks if BibTeX has been run by someone else, and in this case it
		tells the system that it should recompile the document.
		"""
		if exists(self.doc.src_base + ".aux"):
			self.used_cites, self.prev_dbs = self.parse_aux()
		else:
			self.prev_dbs = None
		if self.doc.log.lines:
			self.undef_cites = self.list_undefs()

		self.run_needed = self.first_run_needed()
		if self.doc.must_compile:
			# If a LaTeX compilation is going to happen, it is not necessary
			# to bother with BibTeX yet.
			return 0
		if self.run_needed:
			return self.run()

		bbl = self.base + ".bbl"
		if exists(bbl):
			if getmtime(bbl) > getmtime(self.doc.src_base + ".log"):
				self.doc.must_compile = 1
		return 0

	def first_run_needed (self):
		"""
		The condition is only on the database files' modification dates, but
		it would be more clever to check if the results have changed.
		BibTeXing is also needed when the last run of BibTeX failed, and in
		the very particular case when the style has changed since last
		compilation.
		"""
		if not exists(self.base + ".aux"):
			return 0
		if not exists(self.base + ".blg"):
			return 1

		dtime = getmtime(self.base + ".blg")
		for db in self.db.values():
			if getmtime(db) > dtime:
				msg.log(_("bibliography database %s was modified") % db, pkg="bibtex")
				return 1

		blg = open(self.base + ".blg")
		for line in blg.readlines():
			if re_error.search(line):
				blg.close()
				msg.log(_("last BibTeXing failed"), pkg="bibtex")
				return 1
		blg.close()

		if self.style_changed():
			return 1
		if self.bst_file and getmtime(self.bst_file) > dtime:
			msg.log(_("the bibliography style file was modified"), pkg="bibtex")
			return 1
		return 0

	def parse_aux (self):
		"""
		Parse the aux files and return the list of all defined citations and
		the list of databases used.
		"""
		last = 0
		cites = {}
		dbs = []
		for auxname in self.doc.aux_md5.keys():
			aux = open(auxname)
			for line in aux.readlines():
				match = re_citation.match(line)
				if match:
					cite = match.group("cite")
					if not cites.has_key(cite):
						last = last + 1
						cites[cite] = last
					continue
				match = re_bibdata.match(line)
				if match:
					dbs.extend(match.group("data").split(","))
			aux.close()
		dbs.sort()

		if self.sorted:
			list = cites.keys()
			list.sort()
			return list, dbs
		else:
			list = [(n,c) for (c,n) in cites.items()]
			list.sort()
			return [c for (n,c) in list], dbs

	def list_undefs (self):
		"""
		Return the list of all undefined citations.
		"""
		cites = {}
		for line in self.doc.log.lines:
			match = re_undef.match(line)
			if match:
				cites[match.group("cite")] = None
		list = cites.keys()
		list.sort()
		return list

	def post_compile (self):
		"""
		This method runs BibTeX if needed to solve undefined citations. If it
		was run, then force a new LaTeX compilation.
		"""
		if not self.bibtex_needed():
			msg.log(_("no BibTeXing needed"), pkg="bibtex")
			return 0
		return self.run()

	def run (self):
		"""
		This method actually runs BibTeX with the appropriate environment
		variables set.
		"""
		msg.progress(_("running BibTeX on %s") % self.base)
		doc = {}
		if len(self.bib_path) != 1:
			doc["BIBINPUTS"] = string.join(self.bib_path +
				[os.getenv("BIBINPUTS", "")], ":")
		if len(self.bst_path) != 1:
			doc["BSTINPUTS"] = string.join(self.bst_path +
				[os.getenv("BSTINPUTS", "")], ":")
		if self.env.execute(["bibtex", "--min-crossrefs=100", self.base], doc):
			msg.info(_("There were errors making the bibliography."))
			return 1
		self.run_needed = 0
		self.doc.must_compile = 1
		return 0

	def bibtex_needed (self):
		"""
		Return true if BibTeX must be run.
		"""
		if self.run_needed:
			return 1
		msg.log(_("checking if BibTeX must be run..."), pkg="bibtex")

		new, dbs = self.parse_aux()

		# If there was a list of used citations, we check if it has
		# changed. If it has, we have to rerun.

		if self.prev_dbs is not None and self.prev_dbs != dbs:
			msg.log(_("the set of databases changed"), pkg="bibtex")
			self.prev_dbs = dbs
			self.used_cites = new
			self.undef_cites = self.list_undefs()
			return 1
		self.prev_dbs = dbs

		# If there was a list of used citations, we check if it has
		# changed. If it has, we have to rerun.

		if self.used_cites:
			if new != self.used_cites:
				msg.log(_("the list of citations changed"), pkg="bibtex")
				self.used_cites = new
				self.undef_cites = self.list_undefs()
				return 1
		self.used_cites = new

		# If there was a list of undefined citations, we check if it has
		# changed. If it has and it is not empty, we have to rerun.

		if self.undef_cites:
			new = self.list_undefs()
			if new == []:
				msg.log(_("no more undefined citations"), pkg="bibtex")
				self.undef_cites = new
			else:
				for cite in new:
					if cite in self.undef_cites:
						continue
					msg.log(_("there are new undefined citations"), pkg="bibtex")
					self.undef_cites = new
					return 1
				msg.log(_("there is no new undefined citation"), pkg="bibtex")
				self.undef_cites = new
				return 0
		else:
			self.undef_cites = self.list_undefs()

		# At this point we don't know if undefined citations changed. If
		# BibTeX has not been run before (i.e. there is no log file) we know
		# that it has to be run now.

		blg = self.base + ".blg"
		if not exists(blg):
			msg.log(_("no BibTeX log file"), pkg="bibtex")
			return 1

		# Here, BibTeX has been run before but we don't know if undefined
		# citations changed.

		if self.undef_cites == []:
			msg.log(_("no undefined citations"), pkg="bibtex")
			return 0

		log = self.doc.src_base + ".log"
		if getmtime(blg) < getmtime(log):
			msg.log(_("BibTeX's log is older than the main log"), pkg="bibtex")
			return 1

		return 0

	def clean (self):
		self.doc.remove_suffixes([".bbl", ".blg"])

	#
	# The following method extract information from BibTeX log files.
	#

	def style_changed (self):
		"""
		Read the log file if it exists and check if the style used is the one
		specified in the source. This supposes that the style is mentioned on
		a line with the form 'The style file: foo.bst'.
		"""
		blg = self.base + ".blg"
		if not exists(blg):
			return 0
		log = open(blg)
		line = log.readline()
		while line != "":
			if line[:16] == "The style file: ":
				if line.rstrip()[16:-4] != self.style:
					msg.log(_("the bibliography style was changed"), pkg="bibtex")
					log.close()
					return 1
			line = log.readline()
		log.close()
		return 0

	def get_errors (self):
		"""
		Read the log file, identify error messages and report them.
		"""
		blg = self.base + ".blg"
		if not exists(blg):
			return
		log = open(blg)
		last_line = ""
		line = log.readline()
		while line != "":
			m = re_error.search(line)
			if m:
				# TODO: it would be possible to report the offending code.
				if m.start() == 0:
					text = string.strip(last_line)
				else:
					text = string.strip(line[:m.start()])
				line = m.group("line")
				if line: line = int(line)
				d =	{
					"pkg": "bibtex",
					"kind": "error",
					"text": text
					}
				d.update( m.groupdict() )

				# BibTeX does not report the path of the database in its log.

				file = d["file"]
				if file[-4:] == ".bib":
					file = file[:-4]
				if self.db.has_key(file):
					d["file"] = self.db[file]
				elif self.db.has_key(file + ".bib"):
					d["file"] = self.db[file + ".bib"]
				yield d
			last_line = line
			line = log.readline()
		log.close()
		return
