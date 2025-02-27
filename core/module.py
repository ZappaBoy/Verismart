""" CSeq C Sequentialization Framework
	module stubs

	written by Omar Inverso, University of Southampton.

	CSeq's Translator modules are
	built on top of pycparser, BSD licensed, by Eli Bendersky,
	pycparser embeds PLY, by David M. Beazley,
	maintained by Truc Nguyen Lam, University of Southampton.


Naming conventions for introduced variables (use the following prefixes):
	- __cs_ in general for any variable renamed or introduced in the translation
	- __cs_tmp_ for additional temporary variables (for example, if conditions)

TODO:
	- rename basic modules to Wrappers (to match other documentation)
	- new module that takes C and returns a generic string,
	  not a translator, not a wrapper, but something to extract information on the files,
	  for example to list the names of functions, or to calculate source code complexity
	  according to some metric.

Changelog:
    2017.08.23  fix for extracting line number from node coord (from pycparser 2.18+)
	2015.06.29  fixed coords calculation for parsing errors
	2015.01.07  added  self.stacknodes  as the stack of nodes in the AST currently being visited
	2014.12.09  major reorganisation to accomodate new CSeq framework
	2014.10.22  implemented linemapping (now disabled to improve performance and stability - it needs proper testing before releasing)
	2014.10.15  implemented  self.stack  to examine the stack of calls to visit() (modules may use this)
	2014.06.02  introduced specific  module.ModuleError  exception for detailed error handling

"""
VERSION = 'module-1.0-2017.08.23'
# VERSION = 'module-0.0-2015.07.16'
# VERSION = 'module-0.0-2015.07.03'
#VERSION = 'module-0.0-2015.06.29'
#VERSION = 'module-0.0-2015.01.07'
#VERSION = 'module-0.0-2014.12.24'  # CSeq-1.0beta

import os, re, sys, time
import pycparser.c_parser, pycparser.c_ast, pycparser.c_generator
from core import parser, utils

'''
Notes on line-mapping.

In general, using linemarkers from the beginning and
propagating them across modules,
so that each module would have references to the original input,
can work well.

However since pycparser does not handle line control directives,
we need some workaround to implement this mechanism.

This is a two-step process:

- step 1: while generating the output,
		  coord markers (see cpp line control)
		  are inserted whenever coord information is present.
		  So at a certain line in the output the code will look like:

			  # X             // <-- marker
			  int k = 1239;   // <-- line Y in the output

		  This step is performed in visit().

- step 2: once all the (marked) output is generated,
		  the module does not return its output yet, as
		  pycparser does not support line control.

		  The markers need to be removed from the output,
		  but before that, the information stored in them is used
		  to map input lines to output lines according
		  to the following mechanism.

		  The line number from the marker corresponds to the input line number.
		  The line below the marker is che corresponding line of the output =

			(actual output line including markers) - (number of markers so far).

		  In the example,
		  statement (int k = 1239) from the output file
		  comes from line X of the input file.

		  This is done in generatelinenumbers() at the end of the visit of the AST.

TODO:
	- ...

Changelog:
	2015.07.16  add removelinenumbers to strip off fake include (Truc)

'''


''' Generic module error '''
class ModuleError(Exception):
	def __init__(self, value): self.value = value
	def __str__(self): return repr(self.value)

''' Module requests stopping the translation '''
class ModuleChainBreak(ModuleError): pass

''' Error in module parameters '''
class ModuleParamError(ModuleError): pass


class ModuleParam():
	def __init__(self,id,description='',datatype='',default='',optional=''):
		self.id = id
		self.description = description  # description displayed in the help screen by the front-end
		self.datatype = datatype        # type ('int', 'string') checked by the front-end
		self.default = default          # default value
		self.optional = optional        # optional (=True) or mandatory (=False)?

	def isflag(self):
		return (self.datatype == None or self.datatype == '')

	def tostring(self):
		return '%s, \'%s\', %s, %s, %s' % (self.id, self.description, self.default, self.datatype, 'optional' if self.optional else 'mandatory')


class BasicModule():
	def __init__(self):
		# the CSeq environment
		self.cseqenv = None

		# Input and output of the module
		self.input = ''
		self.output = ''

		# Module parameters
		self.inputparamdefs = []
		self.outputparamdefs = []

	def getversion(self): return self.MODULE_VERSION

	def getname(self): return self.__class__.__name__

	def loadfromstring(self,string,env):
		# At this point the module expects
		# all the input parameters previously set with addInputParam()
		# to have a corresponding entry in the cseq environment.
		#
		for p in self.inputparamdefs:
			if not p.optional and p.id not in env.paramvalues:
				raise ModuleParamError('module \'%s\' requires parameter --%s.' % (self.getname(), p.id))

	def log(self, string):
		tag = 'log:'
		taglen = len(tag)+1
		print(utils.colors.BLUE+tag+utils.colors.NO)
		print(('\n'+' '*taglen).join([l for l in string.split('\n')]))


	def warn(self,string):
		tag = 'warning:'
		taglen = len(tag)+1
		print(utils.colors.YELLOW+tag+utils.colors.NO)
		print(('\n'+' '*taglen).join([l for l in string.split('\n')]))

	def error(self,string):
		tag = 'error:'
		taglen = len(tag)+1

		#raise ModuleParamError(string)

		print(utils.colors.RED+tag+utils.colors.NO)
		print(('\n'+' '*taglen).join([l for l in string.split('\n')]))
		sys.exit(1)

	def getoutput(self): return self.output

	def save(self,filename):
		outfile = open(filename,"w")
		outfile.write(self.output)
		outfile.close()

	''' parameter handling '''
	def initParams(self,env):
		self.cseqenv = env

		# Initialise input parameters when needed
		for p in self.inputparamdefs:
			if not p.optional and p.default and p.id not in env.paramvalues:
				env.paramvalues[p.id] = p.default

	def addInputParam(self,id,description,datatype,default,optional):
		q = ModuleParam(id,description,datatype,default,optional)
		# TODO when adding duplicated input parameters,
		# field 'datatype' should be consistent;
		# all other attributes are left like on the 1st time.
		self.inputparamdefs.append(q)

	def addOutputParam(self,id,description='',datatype=''):
		self.outputparamdefs.append(ModuleParam(id,description,datatype))

	def getInputParamValue(self,id):
		if id in self.cseqenv.paramvalues: return self.cseqenv.paramvalues[id]
		return None

	def setOutputParam(self,id,value): self.cseqenv.paramvalues[id] = value


class Translator(BasicModule,pycparser.c_generator.CGenerator):
	indent_level = 0
	INDENT_SPACING = '    '

	def __init__(self):
		super(Translator,self).__init__()

		# Parser module to generate the AST, the symbol table and other data structs.
		self.Parser = parser.Parser()

		# Coords for the last AST node visited
		self.lastInputCoords = ''         # coords, example: ':245'
		self.currentInputLineNumber = 0   # line numbers extracted from coords

		# Coords last read from a linemarker
		self.lastinputfile = ''      # input file in the last linemarker
		self.lastinputlineno = 0     # line number since last linemarker
		self.lastoutputlineno = 0    # line number in output

		# Stacks of ongoing recursive visits
		self.stack = []       # class names, example: ['FileAST', 'FuncDef', 'Compound', 'Compound', 'If', 'BinaryOp', 'ArrayRef', 'ID']
		self.stacknodes = []  # AST nodes

		self.currentFunct = '' # name of the function being parsed ('' = none)

		self.lines = []

		# Input<->Output linemaps
		self.inputtooutput = {}
		self.outputtoinput = {}


	''' Returns the input line number
		mapping it back from the output of last module to the input of 1st module.

		Returns 0 if unable to map back the line number.
	'''
	def _mapbacklineno(self,lineno):
		# Note: since the same input line may correspond to
		#       multiple lines in the final output,
		#       the tracing has to be done backwards.
		#
		lastmodule = len(self.cseqenv.maps)
		nextkey = 0
		inputfile = ''

		if lineno in self.cseqenv.maps[len(self.cseqenv.maps)-1]:
			#firstkey = lastkey = nextkey = lineno
			firstkey = nextkey = lastkey = lineno

			for modno in reversed(range(0,lastmodule)):
				if nextkey in self.cseqenv.maps[modno] and nextkey != 0:
					lastkey = nextkey
					nextkey = self.cseqenv.maps[modno][nextkey]
				else:
					nextkey = 0

				if modno == 0:
					inputfile = self.cseqenv.outputtofiles[lastkey]

		return (nextkey, inputfile)


	def _make_indent(self,delta=0):
		return (self.indent_level+delta) * self.INDENT_SPACING


	def _getCurrentCoords(self,item):
		linecoord = utils.removeColumnFromCoord(item.coord)
		''' NOTE: uncomment instructions below to disable linemapping '''
		#return ''

		''' NOTE: uncomment instructions below to enable linemapping '''
		# lineno = str(item.coord)[1:] if str(item.coord)[0] == ':' else -1 # not valid from pycparser 2.18+
		lineno = linecoord[1:] if linecoord[0] == ':' else -1
		return '# %s "<previous_module>"' % (lineno)
		#return '# %s \n' % (lineno)


	def insertheader(self,h):
		offset = h.count('\n')
		self.output = h + self.output

		# Shift linemapping accordingly.
		for i in range(1,max(self.inputtooutput)):
			if i in self.inputtooutput:
				self.inputtooutput[i] += offset

		#for i in range(max(self.outputtoinput),1):
		for i in reversed(range(1,max(self.outputtoinput))):
			if i in self.outputtoinput:
				self.outputtoinput[i+offset] = self.outputtoinput[i]
				self.outputtoinput[i] = -1

	def removelinenumbers(self):
		'''
			Strip off fake define include and recalculate line number
		'''
		s2 = ''
		status = 0
		top = bottom = 0

		# print "Input to output"
		# for i in self.inputtooutput:
		#     print "%s -> %s" % (i, self.inputtooutput[i])
		# print "Output to input"
		# for i in self.outputtoinput:
		#     print "%s -> %s" % (i, self.outputtoinput[i])
		# utils.saveFile("beforestrip.c", self.output)

		for i, line in enumerate(self.output.split('\n')):
			if '_____STARTSTRIPPINGFROMHERE_____' in line:
				status = 1
				# print '-----> top line: %s' % (i + 1)
				top = i + 1
				continue

			if '_____STOPSTRIPPINGFROMHERE_____' in line:
				status = 2
				# print '-----> bottom line: %s' % (i + 1)
				bottom = i + 1
				continue

			if status == 0 or status == 2:
				s2 += line + '\n'

		offset = bottom - top + 1

		# input file
		#  |     region 1    |     removed region    |  region 2
		#                    ^        offset         ^
		#                   top                   bottom

		# Shift linemapping accordingly.
		for i in reversed(range(1, max(self.inputtooutput))):
			if i in self.inputtooutput:
				if self.inputtooutput[i] > bottom:
					# Shift back if output line in region 2
					self.inputtooutput[i] -= offset
				elif self.inputtooutput[i] >= top:
					# Map to -1 if output line in removed region
					self.inputtooutput[i] = -1

		# #for i in range(max(self.outputtoinput),1):
		m = max(self.outputtoinput)
		for i in range(top, m):
			if (i + offset) in self.outputtoinput:
				self.outputtoinput[i] = self.outputtoinput[i + offset]
			elif i + offset > m:
				self.outputtoinput[i] = -1

		# print "Input to output"
		# for i in self.inputtooutput:
		#     print "%s -> %s" % (i, self.inputtooutput[i])
		# print "Output to input"
		# for i in self.outputtoinput:
		#     print "%s -> %s" % (i, self.outputtoinput[i])
		# utils.saveFile("afterstrip.c", s2)

		self.output = s2

	def loadfromstring(self,string,env):
		super(Translator,self).loadfromstring(string,env)

		self.input = string
		self.Parser.reset()  # resets all the parser datastructs
		self.Parser.loadfromstring(string)
		self.ast = self.Parser.ast
		self.output = self.visit(self.ast)

		fileno = str(self.cseqenv.transforms+1).zfill(2)

		# Remove any linemarker indentation.
		newoutput = ''

		for line in self.output.splitlines():
			newoutput += re.sub(r'(%s)*#'%self.INDENT_SPACING, r'#', line) + '\n'

		self.markedoutput = newoutput
		self.output = newoutput

		# Generate the linemap and remove linemarkers from self.output
		self.removeemptylines()
		self.generatelinenumbers()


	def getlinenumbertable(self):
		linenumbers = ''

		for i in range(1,self.lastoutputlineno+1):
			if i in self.outputtoinput:
				linenumbers += "%d <- %d\n" % (self.outputtoinput[i],i)

		return linenumbers


	def removeemptylines(self):
		cleanoutput = ''

		for line in self.output.splitlines():
			if line.strip() != '':
				cleanoutput += line + '\n'

		self.output = cleanoutput


	def generatelinenumbers(self):
		''' the difference with the std preprocessor linemapping (see merger.py) is that
			here we assume that when there are no linemarkers the output line
			always generates from the input coordinate fetched from the last linemarker found.
		'''
		inputlineno = 0      # actual input line number including line with markers
		inputmarkercnt = 0   # count the linemarkers in the input (each linemarker takes one line)
		cleanoutput = ''   # output without linemarkers

		for line in self.output.splitlines():
			inputlineno +=1

			if line.startswith('# '):
				inputmarkercnt += 1
				(self.lastinputlineno,self.lastinputfile,self.lastflag) = utils.linemarkerinfo(line)
			else:
				if line == '':   # avoid mapping empty lines
					#print "EMPTY LINE"
					pass
				else:
					#  > > >   Our line map   < < <
					self.inputtooutput[self.lastinputlineno] = inputlineno-inputmarkercnt
					self.outputtoinput[inputlineno-inputmarkercnt] = self.lastinputlineno

				self.lastoutputlineno += 1
				cleanoutput += line + '\n'

		self.output = cleanoutput


	# Extract the coords from an error condition
	#
	def parseErrorCoords(self, error):
		tmp = str(error).split(':')

		try: row = int(tmp[1])
		except ValueError: row = -1

		try: col = int(tmp[2])
		except ValueError: col = -1

		return ":%s:%s" % (row,col)


	def getLineNo(self,error):
		return int(self.parseErrorCoords(error).split(':')[1])


	def getColumnNo(self,error):
		return int(self.parseErrorCoords(error).split(':')[2])


	def visit_FuncDef(self,n):
		if n.decl.name: self.currentFunct = n.decl.name
		funcBlock = super(Translator, self).visit_FuncDef(n)
		if n.decl.name: self.currentFunct = ''

		return funcBlock


	def visit(self,node):
		method = 'visit_' + node.__class__.__name__
		self.stack.append(node.__class__.__name__)
		self.stacknodes.append(node)
		lineCoords = ''

		# Extracts node coords where possible.
		#
		# This is to update the current coord (= filename+line number)
		# of the input being parsed, considering that:
		#
		# - on the same line of input, there may be more AST nodes (shouldn't enter duplicates)
		# - compound statement and empty statements have line number 0 (shouldn't update the current line)
		# - the same line of input may correspond to many lines of output
		#
		if hasattr(node, 'coord'):
			if ((self.stack[-1] == 'Struct' and self.stack[-2] == 'Typedef') or # typedef structs break linemap
				False):
				#(len(self.stack)>=2 and self.stack[-1] != 'Compound' and self.stack[-2] == 'DoWhile')):
				pass
			elif node.coord:
				self.lastInputCoords = utils.removeColumnFromCoord(node.coord)
				# self.lastInputCoords = str(node.coord) # not valid since pycparser 2.18

				# line number handling borrowed from CSeq-0.5,
				# linenumber = str(self.lastInputCoords)
				# linenumber = linenumber[linenumber.rfind(':')+1:]
				linenumber = self.lastInputCoords[1:]
				self.currentInputLineNumber = int(linenumber)

				# Each line of the output is annotated when
				# either it is coming from a new input line number
				# or the input line has generated many output lines,
				# in which case the annotation needs to be repeated at each line..
				#
				if self.currentInputLineNumber != 0 and self.currentInputLineNumber not in self.lines:
					self.lines.append(self.currentInputLineNumber) # now until next input line is read, do not add further markers
					lineCoords = '\n'+self._getCurrentCoords(node)+'\n' #+ '<-' + str(self.stack[-1]) + '\n'

		retval = lineCoords + super(Translator, self).visit(node)

		self.stack.pop()
		self.stacknodes.pop()

		return retval



