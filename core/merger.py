""" CSeq C Sequentialization Framework
    source merging module

    written by Omar Inverso, University of Southampton.
"""
VERSION = 'merger-0.0-2015.10.20'
# VERSION = 'merger-0.0-2015.07.09'
#VERSION = 'merger-0.0-2014.12.24'  # CSeq-1.0beta
#VERSION = 'merger-0.0-2014.10.09'  # newseq-0.6a, newseq-0.6c SVCOMP15
#VERSION = 'merger-0.0-2014.09.27'
#VERSION = 'merger-0.0-2014.09.22'
#VERSION = 'merger-0.0-2014.02.25'
"""
Purpose of this module:

    - input sanitising (e.g. __thread_local workaround, removing non-standard C extensions, ...)
    - merge all the input files into one

    NOTE: the line mapping mechanism is very different from the other modules
         (linemarkers are generated by the external preprocessor and thus follow
          a different logic)

Prerequisites:
    gcc/cpp preprocessor prerequisites

Changelog:
    2017.01.28  add a little fix to statement expression transformation (({...}) --> ...)
    2016.12.19  fix problem when containing directory of lazy-cseq contains spaces in its name
    2016.11.18  fix getsystem header
    2016.09.26  add option to extract system headers
    2015.10.20  patch for ldv-races category of SVCOMP16 (from 2_1 to 3_2)
    2015.07.12  new linemapping now mapping back from (outputline) -> (inputline,inputfile)
    2014.10.09  moved all parser-based code transformations from this module to  workarounds.py
    2014.10.09  modifying sanitise() to avoid using the preprocessor at that stage (easier line mapping)
    2014.09.27  new method sanitise() now includes all code-satinising, including thread_local workaround
    2014.09.22  moved thread local workaround from  new.py  to this module
    2014.09.22  load() / loadfromstring() now use gcc rather than cpp to preprocess,
                needed to avoid problems with OSX (where cpp does not remove C++-style comments, causing parsing errors)
    2014.02.25  switched to  module.Module  base class for modules

"""

import pycparser.c_ast, pycparser.c_generator, pycparser.c_parser
from core import common, module, utils
import getopt, inspect, os, re, sys, subprocess, shlex, time


class Merger(module.Translator):
    __parsingFunction = False
    currentAnonStructsCount = 0   # counts the number of anonymous structures (used to assign consecutive names)

    inputtooutput = {}        # input lines to output lines
    outputtoinput = {}        # output lines to input lines
    outputtofiles = {}        # output lines to input file names

    need_gnu_fix = False


    def __init__(self):
        pass


    def _gnu_extension_fix(self, input):
        text = ''
        ''' 1. Fix Empty Structure (partial for some particular benchmarks in svcomp16)
        https://gcc.gnu.org/onlinedocs/gcc/Empty-Structures.html
        '''
        text = input.replace('''\
struct device {

};''', '''\
struct device {
    char unused;
};''')

        ''' 2. Fix typeof (partial for some particular benchmarks in svcomp16)
        https://gcc.gnu.org/onlinedocs/gcc/Typeof.html#Typeof
        '''
        text = text.replace("typeof( ((struct my_data *)0)->dev )", "struct device ")

        ''' 3. Fix Statement Expression (partial for some particular benchmarks in svcomp16)
        https://gcc.gnu.org/onlinedocs/gcc/Statement-Exprs.html#Statement-Exprs
        '''
        ret = ''
        for l in text.splitlines():
            if "({" in l:
                m = re.match(r'^(.+)\({(.*)}\)(.+)$', l)
                newline = ''
                if m:
                    firstpart = m.group(1)
                    secondpart = m.group(2)
                    thirdpart = m.group(3)
                    stmts = secondpart.split(";")
                    stmts = stmts[:-1]
                    firstpart += stmts[-1] + thirdpart
                    stmts = stmts[:-1]
                    for s in stmts:
                        newline += s + '; '
                    newline += firstpart
                else:
                    newline = l
                ret += newline + '\n'
            else:
                ret += l + '\n'

        return ret

    def _thread_local_fix(self, input):
        text = ''
        ''' 1. Fix thread local, and remove some useless statements
        '''
        ret = ''
        for line in input.splitlines():
            line = re.sub(r'__thread unsigned int (.*);', r'unsigned int __cs_thread_local_\1[THREADS+1];', line)
            line = re.sub(r'__thread int (.*);', r'int __cs_thread_local_\1[THREADS+1];', line)
            line = line.replace('do { } while (0);', ';')
            ret += line + '\n'

        return ret

    def isSystemHeader(self, filename):
        if filename.startswith('smack'):
            return False
        fake_include = os.path.dirname(__file__)+'/include/'
        if os.path.isfile(fake_include+filename):
            return True
        else:
            return False

    def getSystemHeaders(self, input):
        ret = ''
        for l in input.splitlines():
            m = re.match(r'[ \t]*#[ \t]*include[ \t]*[\"<](.+)[\">]', l)
            if m:
                header = m.group(1)
                if (self.isSystemHeader(header) and
                        header != 'pthread.h'):
                    ret += "#include <%s>\n" % header

        return ret

    ''' Performs a series of simple transformations to make sure that pycparser can handle the input.
    '''
    def _sanitise(self, input):
        # Transformation 1:
        #    _thread_local workaround, step I
        #    (step II is performed later by the corresponding  threadlocal.py  module)
        #
        text = ''

        for line in input.splitlines():
            line = re.sub(r'__thread _Bool (.*) = 0', r'_Bool __cs_thread_local_\1[THREADS+1] ', line)
            line = re.sub(r'_Thread_local _Bool (.*) = 0', r'_Bool __cs_thread_local_\1[THREADS+1] ', line)

            # fix for void; line
            line = re.sub(r'^void;', '', line)

            if not self.need_gnu_fix and "typeof" in line:
                self.need_gnu_fix = True

            text += line+'\n'

        return text


    def loadfromstring(self, string, env):
        self.cseqenv = env
        self.input = string

        # set system header
        setattr(env, "systemheaders", self.getSystemHeaders(string))

        string = self._sanitise(string)

        # Run the preprocessor with linemarker generation.
        #
        # the linemarker has the following format
        # (see https://gcc.gnu.org/onlinedocs/gcc-4.3.6/cpp/Preprocessor-Output.html):
        #
        #   # LINE_NO FILENAME FLAG
        #
        # examples:
        #  # 1 "<stdin>"
        #  # 1 "include/pthread.h" 2
        #
        includestring = ''

        localincludepath = ''
        if '/' in env.inputfile:
            localincludepath = env.inputfile[:env.inputfile.rfind('/')]
        if localincludepath !='':
            localincludepath = ' -I\"%s\"' % localincludepath

        # Workaround:
        # cpp does not strip C++-style comments ('//') from the input code,
        # gcc works instead.
        #
        includestring += ' -I\"%s' % os.path.dirname(__file__)+'/include\"' # include fake headers first

        includestring += localincludepath

        if env.includepath:
            includestring += ' -I' + ' -I'.join(env.includepath.split(':'))

        # Pre-process away GNU C extensions.
        macros = "-D'__attribute__(x)=' -D'__extension__(x)=' -D'__volatile__=' -D'__asm__=' -D'__attribute(x)=' "

        #cmdline = 'cpp -Iinclude -E -C ' + filename + ' > ' + filename + '.1' + '.c'
        #cmdline = 'gcc -Iinclude -P -E - '  # hyphen at the end forces input from stdin
        cmdline = 'gcc %s -nostdinc %s -E - ' % (macros,includestring) # hyphen at the end forces input from stdin
        # print(cmdline)
        p = subprocess.Popen(shlex.split(cmdline), stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        string = (p.communicate(input=string.encode())[0]).decode()

        if self.need_gnu_fix:
            string = self._gnu_extension_fix(string)

        string = self._thread_local_fix(string)

        # After preprocessing, line markers indicate where the lines come from, but
        # this information needs to be removed from the input block, or
        # pycparser won't handle it.
        #
        input = string.splitlines()

        output = ''             # clean input (without linemarkers)
        outputlineno = 0        # current output line number (clean, no linemarkers)
        inputlinenooffset = 0   # number of input lines since the last marker

        # coords fetched from the last linemarker
        lastinputfile = ''      # input file from the last linemarker
        lastinputlineno = 0     # line number from the last linemarker

        for line in input:
            if line.startswith('# '):
                inputlinenooffset = 0
                (lastinputlineno,lastinputfile,lastflag) = utils.linemarkerinfo(line)
            else:
                #  > > >   Our line map   < < <
                self.outputtoinput[outputlineno] = lastinputlineno+inputlinenooffset-1
                self.outputtofiles[outputlineno] = lastinputfile if lastinputfile!='<stdin>' else env.inputfile
                #print "%s,%s <- %s" % (self.outputtoinput[outputlineno],self.outputtofiles[outputlineno],outputlineno)
                inputlinenooffset += 1
                outputlineno += 1

                output += line + '\n'

        self.markedoutput = string
        self.output = output
        self.lastoutputlineno = outputlineno


    def getlinenumbertable(self):
        str = ''

        for i in range(1,self.lastoutputlineno):
            if i in self.outputtoinput:
                str += "%s,%s <- %s\n" % (self.outputtoinput[i],self.outputtofiles[i], i)

        return str


    def save(self, filename):
        outfile = open(filename,"w")
        outfile.write(self.output)
        outfile.close()


    def show(self):
        print(self.output)


    def getoutput(self):
        return self.output






