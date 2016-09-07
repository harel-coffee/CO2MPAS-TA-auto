#!/usr/b in/env python
#
# Copyright 2014-2015 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
#
"""A *project* stores all CO2MPAS files for a single vehicle, and tracks its sampling procedure. """

from collections import (defaultdict, OrderedDict, namedtuple)  # @UnusedImport
import copy
from datetime import datetime
import inspect
import io
import json
import os
from pandalone import utils as pndlutils
import textwrap
from typing import (
    Any, Union, List, Dict, Sequence, Iterable, Optional, Text, Tuple, Callable)  # @UnusedImport

import git  # From: pip install gitpython
from toolz import itertoolz as itz, dicttoolz as dtz
import transitions

from co2mpas import __uri__  # @UnusedImport
from co2mpas import utils
from co2mpas._version import (__version__, __updated__, __file_version__,   # @UnusedImport
                              __input_file_version__, __copyright__, __license__)  # @UnusedImport
from co2mpas.sampling import baseapp, dice, report
from co2mpas.sampling.baseapp import convpath, ensure_dir_exists, where, first_line
import functools as fnt
import os.path as osp
import traitlets as trt
import traitlets.config as trtc



###################
##     Specs     ##
###################

PROJECT_VERSION = '0.0.2'  ## TODO: Move to `co2mpas/_version.py`.

def split_version(v):
    return v.split('.')

PROJECT_STATUSES = '<invalid> empty full signed dice_sent sampled'.split()

_CommitMsg = namedtuple('_CommitMsg', 'project state action msg_version')

_PROJECTS_PREFIX = 'projects/'
_HEADS_PREFIX = 'refs/heads/'
_PROJECTS_FULL_PREFIX = _HEADS_PREFIX + _PROJECTS_PREFIX

def _is_project_ref(ref: git.Reference) -> bool:
    return ref.name.startswith(_PROJECTS_PREFIX)

def _ref2pname(ref: git.Reference) -> Text:
    return ref.path[len(_PROJECTS_FULL_PREFIX):]

def _pname2ref_path(pname: Text) -> Text:
    if pname.startswith(_HEADS_PREFIX):
        pass
    elif not pname.startswith(_PROJECTS_PREFIX):
        pname = '%s%s' % (_PROJECTS_FULL_PREFIX, pname)
    return pname

def _pname2ref_name(pname: Text) -> Text:
    if pname.startswith(_HEADS_PREFIX):
        pname = pname[len(_HEADS_PREFIX):]
    elif not pname.startswith(_PROJECTS_PREFIX):
        pname = '%s%s' % (_PROJECTS_PREFIX, pname)
    return pname

def _get_ref(refs, refname: Text, default: git.Reference=None) -> git.Reference:
    return refname and refname in refs and refs[refname] or default

_TAGS_PREFIX = 'refs/tags/'
_DICES_PREFIX = 'dices/'

def _tname2ref_name(tname: Text) -> Text:
    if tname.startswith(_TAGS_PREFIX):
        tname = tname[len(_TAGS_PREFIX):]
    elif not tname.startswith(_DICES_PREFIX):
        tname = '%s%s' % (_DICES_PREFIX, tname)
    return tname


@fnt.lru_cache()
def project_zygote(): #-> Project:
    """Cached Project FSM, for speeding-up its creation; "fork" it before use. """
    return Project('<zygote>', None)


#transitions.logger.level = 50 ## FSM logs annoyingly high.

class Project(transitions.Machine, baseapp.Spec):
    """The Finite State Machine for the currently project."""

    @trt.observe('log_level')
    def set_fsm_loglevel(self, change):
        transitions.logger.level = change['new']

    def fork(self, pname, projects_db, config):
        """
        Optimization, along with :func:`project_zygote`, to speedup FSM creation.

        For an example, see ::meth:`ProjectsDB._conceive_project()`.
        INFO: set here any non-serializable fields for :func:`fnt.lru_cache()` to work.
        """
        clone = copy.deepcopy(self)
        clone.pname = pname
        clone.id = pname + ": "
        clone.projects_db = projects_db
        clone.update_config(self.config)

        return clone

    def __str__(self, *args, **kwargs):
        #TODO: Obey verbosity on project-str.
        return 'Project(%s: %s)' % (self.pname, self.state)

    def _is_force(self, event):
        return event.kwargs.get('force', self.force)

    def _is_inp_files(self, event):
        iofiles = event.kwargs.get('iofiles')
        assert iofiles, ('iofiles', iofiles, event.kwargs)
        return bool(iofiles and iofiles.inp and
                    not (iofiles.out or iofiles.other))

    def _is_out_files(self, event):
        iofiles = event.kwargs.get('iofiles')
        assert iofiles, ('iofiles', iofiles, event.kwargs)
        return bool(iofiles and iofiles.out and
                    not (iofiles.inp or iofiles.other))

    def _is_inp_out_files(self, event):
        iofiles = event.kwargs.get('iofiles')
        assert iofiles, ('iofiles', iofiles, event.kwargs)
        return bool(iofiles and iofiles.inp and iofiles.out
                    and not iofiles.other)

    def _is_other_files(self, event):
        iofiles = event.kwargs.get('iofiles')
        assert iofiles, ('iofiles', iofiles, event.kwargs)
        return bool(iofiles and iofiles.other
                    and not (iofiles.inp or iofiles.out))

    def _is_dice_yes(self, event):
        timestamped_email = event.kwargs.get('timestamped_email')
        assert timestamped_email, ('timestamped_email', timestamped_email, event.kwargs)
        decide_dice(timestamped_email)

    def __init__(self, pname, projects_db, **kwds):
        """DO NOT INVOKE THIS; create it through :meth:`ProjectsDB._conceive_project()`."""
        self.pname = pname
        self.projects_db = projects_db
        states = [
            'UNBOUND', 'INVALID', 'empty', 'wltp_out', 'wltp_inp', 'wltp', 'tagged',
            'mailed', 'dice_yes', 'dice_no', 'nedc',
        ]
        trans = [
            # Trigger        Source-state   Dest-state      Conditions          Unless  Before
            ['create',      'UNBOUND',      'empty'],

            ['import_iof',  'empty',        'wltp',         '_is_inp_out_files'],
            ['import_iof',  'empty',        'wltp_inp',     '_is_inp_files'],
            ['import_iof',  'empty',        'wltp_out',     '_is_out_files'],

            ['import_iof',  ['wltp_inp',
                             'wltp_out',
                             'wltp',
                             'tagged'],     'wltp',         ['_is_inp_out_files', '_is_force']],

            ['import_iof',  'wltp_inp',     'wltp_inp',     ['_is_inp_files', '_is_force']],
            ['import_iof',  'wltp_inp',     'wltp',         '_is_out_files'],

            ['import_iof',  'wltp_out',     'wltp_out',     ['_is_out_files', '_is_force']],
            ['import_iof',  'wltp_out',     'wltp',         '_is_inp_files'],

            ['tag',         'wltp',         'tagged'],

            ['send_email',  'tagged',       'mailed'],
            ['recv_email',  'mailed',       'dice_yes',     '_is_dice_yes'],
            ['recv_email',  'mailed',       'dice_no'],

            ['import_iof',  ['dice_yes',
                             'dice_no'],    'nedc',          '_is_other_files'],
            ['import_iof',  'nedc',         'nedc',         ['_is_other_files', '_is_force']],
        ]
        super().__init__(states=states,
                         initial=states[0],
                         transitions=trans,
                         send_event=True,
                         after_state_change=self._cb_commit_or_tag,
                         auto_transitions=False,
                         name=pname,
                         **kwds)
        self.on_enter_empty(    self._cb_stage_new_project_content)
        self.on_enter_tagged(   self._cb_generate_report)
        self.on_enter_wltp_inp( self._cb_stage_iofiles)
        self.on_enter_wltp_out( self._cb_stage_iofiles)
        self.on_enter_wltp(     self._cb_stage_iofiles)
        self.on_enter_nedc(     self._cb_stage_iofiles)

    def _make_commit_msg(self, action):
        action = '\n'.join(textwrap.wrap(action, width=50))
        cmsg = _CommitMsg(self.pname, self.state, action, PROJECT_VERSION)
        return json.dumps(cmsg._asdict(), indent=2)

    @classmethod
    def parse_commit_msg(self, cmsg_js, scream=False):
        """
        :return: a :class:`_CommitMsg` instance, or fails if cannot parse.
        """
        return json.loads(cmsg_js,
                object_hook=lambda seq: _CommitMsg(**seq))

    def _make_tag_msg(self, report):
        """
        :param report: a list of extracted params
        """
        ## TODO: Report can be more beautiful...
        report_str = '\n\n'.join(report)

        msg = textwrap.dedent("""
        Report for CO2MPAS-project: %r
        ======================================================================
        %s
        """) % (self.pname, report)

        return msg

    def _cb_commit_or_tag(self, event):
        """Executed on ENTER for all states."""
        state = self.state
        if state.islower():
            repo = self.projects_db.repo
            if state == 'tagged':
                self.log.debug('Tagging %s...', event.kwargs)
                assert 'report' in event.kwargs, ('report', event.kwargs)
                report = event.kwargs['report']
                msg = self._make_tag_msg(self.pname, report)
                tref = _tname2ref_name(self.pname)
                repo.create_tag(tref, msg=msg) ## TODO: GPG!!
            else:
                self.log.debug('Committing %s...', event.kwargs)
                assert 'action' in event.kwargs, ('action', event.kwargs)
                action = event.kwargs['action']
                index = repo.index
                cmsg_js = self._make_commit_msg(action)
                index.commit(cmsg_js)

    def _make_readme(self):
        return textwrap.dedent("""
        This is the CO2MPAS-project %r (see https://co2mpas.io/ for more).

        - created: %s
        """ %(self.pname, datetime.now()))

    def _cb_stage_new_project_content(self, event):
        """Create a new project."""
        repo = self.projects_db.repo
        index = repo.index
        state_fpath = osp.join(repo.working_tree_dir, 'CO2MPAS')
        with io.open(state_fpath, 'wt') as fp:
            fp.write(self._make_readme())
        index.add([state_fpath])

        ## Commit/tag callback expects `action` on event.
        event.kwargs['action'] = 'Project created.' ## TODO: Improve actions

    def _new_report_spec(self):
        if not getattr(self, '__report', None):
            self.__report = report.Report(config=self.config)
        return self.__report

    def _cb_stage_iofiles(self, event):
        """
        Triggered by `import_iof()` on ENTER for all `wltp_XX` & 'nedc' states.

        :param dice.IOFiles iofiles:
            what to import
        """
        import shutil

        iofiles = event.kwargs['iofiles']
        force = event.kwargs.get('force', self.force)

        ## Check extraction of report works ok.
        #
        try:
            rep = self._new_report_spec()
            list(rep.yield_from_iofiles(iofiles))
        except Exception as ex:
            msg = ("Failed extracting report from %s, due to: %s"
                   "  BUT FORCED to import them!")
            if force:
                self.log.error(msg, iofiles, ex, exc_info=1)
            else:
                raise baseapp.CmdException(msg % (iofiles, ex))

        repo = self.projects_db.repo
        index = repo.index
        nimported = 0
        for io_kind, fpaths in iofiles._asdict().items():
            for ext_fpath in fpaths:
                self.log.debug('Importing WLTP %s-file: %s', io_kind, ext_fpath)
                assert ext_fpath, "Import none as %s file!" % io_kind

                ext_fname = osp.split(ext_fpath)[1]
                index_fpath = osp.join(repo.working_tree_dir, io_kind, ext_fname)
                ensure_dir_exists(osp.split(index_fpath)[0])
                shutil.copy(ext_fpath, index_fpath)
                index.add([index_fpath])
                nimported += 1

        ## Commit/tag callback expects `action` on event.
        event.kwargs['action'] = 'Imported %d IO-files.' % nimported

    def list_iofiles(self, *io_kinds: Union[dice.IOKind, Any]) -> dice.IOFiles or None:
        """
        :param io_kinds:
            What files to fetch; by default if none specified,
            fetches all: inp,  out, other
            Use this to fetch some::

                self.list_io_files(dice.IOKind.inp, dice.IOKind.out)

        :return:
            A class:`dice.IOFiles` containing list of working-dir paths
            for any WLTP files, or none if none exists.
        """
        if not io_kinds:
            io_kinds = dice.IOKind.__members__  # @UndefinedVariable
        io_kinds = [k.name if isinstance(k, dice.IOKind) else str(k)
                    for k in io_kinds]

        repo = self.projects_db.repo
        def collect_kind_files(io_kind):
            if io_kind:
                wd_fpath = osp.join(repo.working_tree_dir, io_kind)
                fpaths = os.listdir(wd_fpath) if osp.isdir(wd_fpath) else []
                return [(io_kind, osp.join(wd_fpath, f))
                        aaAfor f in fpaths]

        iofpaths = [collect_kind_files(io_kind) for io_kind in io_kinds]
        if any(iofpaths):
            return dice.IOFiles(*iofpaths)

    def _cb_generate_report(self, event):
        """Triggered by `tag()`, builds the report to use as tag-msg."""
        rep = self._new_report_spec()
        iofiles = self.list_iofiles(dice.IOKind.inp, dice.IOKind.out)

        report = list(rep.yield_from_iofiles(iofiles))

        ## Commit/tag callback expects `report` on event.
        event.kwargs['report'] = report




class ProjectsDB(trtc.SingletonConfigurable, baseapp.Spec):
    """A git-based repository storing the TA projects (containing signed-files and sampling-resonses).

    It handles checkouts but delegates index modifications to `Project` spec.

    ### Git Command Debugging and Customization:

    - :envvar:`GIT_PYTHON_TRACE`: If set to non-0,
      all executed git commands will be shown as they happen
      If set to full, the executed git command _and_ its entire output on stdout and stderr
      will be shown as they happen

      NOTE: All logging is outputted using a Python logger, so make sure your program is configured
      to show INFO-level messages. If this is not the case, try adding the following to your program:

    - :envvar:`GIT_PYTHON_GIT_EXECUTABLE`: If set, it should contain the full path to the git executable, e.g.
      ``c:\Program Files (x86)\Git\bin\git.exe on windows`` or ``/usr/bin/git`` on linux.
    """

    repo_path = trt.Unicode('repo',
            help="""
            The path to the Git repository to store TA files (signed and exchanged).
            If relative, it joined against default config-dir: '{confdir}'
            """.format(confdir=baseapp.default_config_dir())).tag(config=True)
    reset_settings = trt.Bool(False,
            help="""
            When enabled, re-writes default git's config-settings on app start up.
            Git settings include user-name and email address, so this option might be usefull
            when the regular owner running the app has changed.
            """).tag(config=True)

    ## Useless, see https://github.com/ipython/traitlets/issues/287
    # @trt.validate('repo_path')
    # def _normalize_path(self, proposal):
    #     repo_path = proposal['value']
    #     if not osp.isabs(repo_path):
    #         repo_path = osp.join(default_config_dir(), repo_path)
    #     repo_path = convpath(repo_path)
    # return repo_path

    __repo = None

    def _setup_repo(self, repo_path):
        if self.__repo:
            if self.__repo.working_dir == repo_path:
                self.log.debug('Reusing repo %r...', repo_path)
                return
            else:
                ## Clean up old repo,
                #  or else... https://github.com/gitpython-developers/GitPython/issues/508
                self.__repo.git.clear_cache()
                ## Xmm, nai...
                self._current_project = None

        if not osp.isabs(repo_path):
            repo_path = osp.join(baseapp.default_config_dir(), repo_path)
        repo_path = convpath(repo_path)
        ensure_dir_exists(repo_path)
        try:
            self.log.debug('Opening repo %r...', repo_path)
            self.__repo = git.Repo(repo_path)
            if self.reset_settings:
                self.log.info('Resetting to default settings of repo %r...',
                              self.__repo.git_dir)
                self._write_repo_configs()
        except git.InvalidGitRepositoryError as ex:
            self.log.info("...failed opening repo '%s',\n  initializing a new repo %r instead...",
                          ex, repo_path)
            self.__repo = git.Repo.init(repo_path)
            self._write_repo_configs()

    @trt.observe('repo_path')
    def _repo_path_changed(self, change):
        self.log.debug('CHANGE repo %r-->%r...', change['old'], change['new'])
        self._setup_repo(change['new'])

    @property
    def repo(self):
        if not self.__repo:
            self._setup_repo(self.repo_path)
        return self.__repo

    def _write_repo_configs(self):
        with self.repo.config_writer() as cw:
            cw.set_value('core', 'filemode', False)
            cw.set_value('core', 'ignorecase', False)
            cw.set_value('user', 'email', self.user_email)
            cw.set_value('user', 'name', self.user_name)
            cw.set_value('alias', 'lg',
                     r"log --graph --abbrev-commit --decorate --date=relative --format=format:'%C(bold blue)%h%C(reset) "
                     r"- %C(bold green)(%ar)%C(reset) %C(white)%s%C(reset) %C(dim white)- "
                     r"%an%C(reset)%C(bold yellow)%d%C(reset)' --all")

    def read_git_settings(self, prefix: Text=None, config_level: Text=None):# -> List(Text):
        """
        :param prefix:
            prefix of all settings.key (without a dot).
        :param config_level:
            One of: ( system | global | repository )
            If None, all applicable levels will be merged.
            See :meth:`git.Repo.config_reader`.
        :return: a list with ``section.setting = value`` str lines
        """
        settings = defaultdict(); settings.default_factory = defaultdict
        sec = '<not-started>'
        cname = '<not-started>'
        try:
            with self.repo.config_reader(config_level) as conf_reader:
                for sec in conf_reader.sections():
                    for cname, citem in conf_reader.items(sec):
                        s = settings
                        if prefix:
                            s = s[prefix]
                        s[sec][cname] = citem
        except Exception as ex:
            self.log.info('Failed reading git-settings on %s.%s due to: %s',
                     sec, cname, ex, exc_info=1)
            raise
        return settings

    def repo_backup(self, folder: Text='.', repo_name: Text='co2mpas_repo',
                    force: bool=None) -> Text:
        """
        :param folder: The path to the folder to store the repo-archive in.
        :return: the path of the repo-archive
        """
        import tarfile

        if force is None:
            force = self.force

        now = datetime.now().strftime('%Y%m%d-%H%M%S%Z')
        repo_name = '%s-%s' % (now, repo_name)
        repo_name = pndlutils.ensure_file_ext(repo_name, '.txz')
        repo_name_no_ext = osp.splitext(repo_name)[0]
        archive_fpath = convpath(osp.join(folder, repo_name))
        basepath, _ = osp.split(archive_fpath)
        if not osp.isdir(basepath) and not force:
            raise FileNotFoundError(basepath)
        ensure_dir_exists(basepath)

        self.log.debug('Archiving repo into %r...', archive_fpath)
        with tarfile.open(archive_fpath, "w:xz") as tarfile:
            tarfile.add(self.repo.working_dir, repo_name_no_ext)

        return archive_fpath

    @fnt.lru_cache() # x6(!) faster!
    def _infos_dsp(self, fallback_value='<invalid>'):
        from co2mpas.dispatcher import Dispatcher
        from co2mpas.dispatcher.utils.dsp import DFun

        dfuns = [
            DFun('repo',            lambda _infos: self.repo),
            DFun('git_cmds',        lambda _infos: where('git')),
            DFun('dirty',           lambda repo: repo.is_dirty()),
            DFun('untracked',       lambda repo: repo.untracked_files),
            DFun('wd_files',        lambda repo: os.listdir(repo.working_dir)),
            DFun('branch',          lambda repo, _inp_prj:
                                        _inp_prj and _get_ref(repo.heads, _pname2ref_path(_inp_prj)) or repo.active_branch),
            DFun('head',            lambda repo: repo.head),
            DFun('heads_count',     lambda repo: len(repo.heads)),
            DFun('projects_count',  lambda repo: itz.count(self._yield_project_refs())),
            DFun('tags_count',      lambda repo: len(repo.tags)),
            DFun('git_version',     lambda repo: '.'.join(str(v) for v in repo.git.version_info)),
            DFun('git_settings',    lambda repo: self.read_git_settings()),

            DFun('head_ref',      lambda head: head.reference),
            DFun('head_valid',      lambda head: head.is_valid()),
            DFun('head_detached',   lambda head: head.is_detached),

            DFun('cmt',             lambda branch: branch.commit),
            DFun('head',            lambda branch: branch.path),
            DFun('branch_valid',    lambda branch: branch.is_valid()),
            DFun('branch_detached', lambda branch: branch.is_detached),

            DFun('tre',             lambda cmt: cmt.tree),
            DFun('author',          lambda cmt: '%s <%s>' % (cmt.author.name, cmt.author.email)),
            DFun('last_cdate',      lambda cmt: str(cmt.authored_datetime)),
            DFun('commit',          lambda cmt: cmt.hexsha),
            DFun('revs_count',      lambda cmt: itz.count(cmt.iter_parents())),
            DFun('cmsg',            lambda cmt: cmt.message),
            DFun('cmsg',            lambda cmt: '<invalid: %s>' % cmt.message, weight=10),

            DFun(['msg.%s' % f for f in _CommitMsg._fields],
                                    lambda cmsg: Project.parse_commit_msg(cmsg)),

            DFun('tree',            lambda tre: tre.hexsha),
            DFun('files_count',     lambda tre: itz.count(tre.list_traverse())),
        ]
        dsp = Dispatcher()
        DFun.add_dfuns(dfuns, dsp)
        return dsp

    @fnt.lru_cache()
    def _out_fields_by_verbose_level(self, level):
        """
        :param level:
            If ''> max-level'' then max-level assumed, negatives fetch no fields.
        """
        verbose_levels = {
            0:[
                'msg.project',
                'msg.state',
                'msg.action',
                'revs_count',
                'files_count',
                'last_cdate',
                'author',
            ],
            1: [
                'infos',
                'cmsg',
                'head',
                'dirty',
                'commit',
                'tree',
                'repo',
            ],
            2: None,  ## null signifies "all fields".
        }
        max_level = max(verbose_levels.keys())
        if level > max_level:
            level = max_level
        fields = []
        for l  in range(level + 1):
            fs = verbose_levels[l]
            if not fs:
                return None
            fields.extend(fs)
        return fields

    def _infos_fields(self, pname: Text=None, fields: Sequence[Text]=None, inv_value=None) -> List[Tuple[Text, Any]]:
        """Runs repo examination code returning all requested fields (even failed ones)."""
        dsp = self._infos_dsp()
        inputs = {'_infos': 'ok', '_inp_prj': pname}
        infos = dsp.dispatch(inputs=inputs,
                             outputs=fields,
                             shrink=True)
        fallbacks = {d: inv_value for d in dsp.data_nodes.keys()}
        fallbacks.update(infos)
        infos = fallbacks

        infos = dict(utils.stack_nested_keys(infos))
        infos = dtz.keymap(lambda k: '.'.join(k), infos)
        if fields:
            infos = [(f, infos.get(f, inv_value))
                     for f in fields]
        else:
            infos = sorted((f, infos.get(f, inv_value))
                           for f in dsp.data_nodes.keys())

        return infos

    def proj_examine(self, pname: Text=None, verbose=None, as_text=False, as_json=False):
        """
        Does not validate project, not fails, just reports situation.

        :param pname:
            Use current branch if unspecified; otherwise, DOES NOT checkout pname.
        :retun: text message with infos.
        """

        if verbose is None:
            verbose = self.verbose
        verbose_level = int(verbose)

        fields = self._out_fields_by_verbose_level(verbose_level)
        infos = self._infos_fields(pname, fields)

        if as_text:
            if as_json:
                infos = json.dumps(dict(infos), indent=2, default=str)
            else:
                #import pandas as pd
                #infos = pd.Series(OrderedDict(infos))
                infos = baseapp.format_pairs(infos)

        return infos

    _current_project = None

    def _conceive_project(self, pname): # -> Project:
        """Returns an "UNBOUND" :class:`Project`; its state must be triggered immediately."""
        return project_zygote().fork(pname, self, self.config)

    def current_project(self) -> Project:
        """
        Returns the current :class:`Project`, or raises a help-msg if none exists yet.

        The project returned is appropriately configured according to its recorded state.
        The git-repo is not touched.
        """
        if not self._current_project:
            try:
                headref = self.repo.active_branch
                if _is_project_ref(headref):
                    pname = _ref2pname(headref)
                    p = self._conceive_project(pname)
                    cmsg = p.parse_commit_msg(headref.commit.message)
                    p.set_state(cmsg.state)

                    self._current_project = p
            except Exception as ex:
                self.log.warning("Failure while getting current-project: %s",
                                 ex, exc_info=1)

        if not self._current_project:
                raise baseapp.CmdException(textwrap.dedent("""
                        No current-project exists yet!"
                        Try opening an existing project, with:
                            co2mpas project open <project-name>
                        or create a new one, with:
                            co2mpas project add <project-name>
                        """))

        return self._current_project

    def proj_add(self, pname: Text) -> Project:
        """
        Creates a new project and sets it as the current one.

        :param pname: the project name
        :return: the current :class:`Project` or fail
        """
        self.log.info('Creating project %r...', pname)
        if not pname or not pname.isidentifier():
            raise baseapp.CmdException('Invalid name %r for a project!' % pname)
        prefname = _pname2ref_name(pname)
        if prefname in self.repo.heads:
            raise baseapp.CmdException('Project %r already exists!' % pname)

        p = self._conceive_project(pname)
        self.repo.git.checkout(prefname, orphan=True, force=self.force)
        ## Trigger ProjectFSM methods that will modify Git-index & commit.
        p.create()
        # FIXME: Revert if fail?

        self._current_project = p
        return p

    def proj_open(self, pname: Text) -> Project:
        """
        :param pname: some branch ref
        :return: the current :class:`Project`
        """
        prefname = _pname2ref_name(pname)
        if prefname not in self.repo.heads:
            raise baseapp.CmdException('Project %r not found!' % pname)
        self.repo.heads[_pname2ref_name(pname)].checkout()

        self._current_project = None
        return self.current_project()

    def _yield_project_refs(self, *pnames: Text):
        if pnames:
            pnames =  [_pname2ref_path(p) for p in pnames]
        for ref in self.repo.heads:
            if _is_project_ref(ref) and not pnames or ref.path in pnames:
                yield ref

    def proj_list(self, *pnames: Text, verbose=None, as_text=False):
        """
        :param pnames: some project name, or none for all
        :param verbose: return infos in a table with 3-4 coulmns per each project
        :retun: yield any matched projects, or all if `pnames` were empty.
        """
        import pandas as pd
        if verbose is None:
            verbose = self.verbose

        res = {}
        for ref in self._yield_project_refs(*pnames):
            pname = _ref2pname(ref)
            infos = []
            if verbose:
                infos = OrderedDict(self._infos_fields(
                        pname=pname,
                        fields='msg.state revs_count files_count last_cdata author msg.action'.split(),
                        inv_value='<invalid>'))
            res[pname] = infos

        if not res:
            res = None
        else:
            ap = self.repo.active_branch
            ap = ap and ap.path
            if verbose:
                res = pd.DataFrame.from_dict(res, orient='index')
                res = res.sort_index()
                res.index = [('* %s' if _pname2ref_path(r) == ap else '  %s') % r
                             for r in res.index]
                res.reset_index(level=0, inplace=True)
                renner = lambda c: c[len('msg.'):] if c.startswith('msg.') else c
                res = res.rename_axis(renner, axis='columns')
                res = res.rename_axis({
                    'index': 'project',
                    'revs_count': '#revs',
                    'files_count': '#files'
                }, axis='columns')
                if as_text:
                    res = res.to_string(index=False)
            else:
                res = [('* %s' if _pname2ref_path(r) == ap else '  %s') % r
                for r in sorted(res)]

        return res



###################
##    Commands   ##
###################

class _PrjCmd(baseapp.Cmd):
    @property
    def projects_db(self):
        p = ProjectsDB.instance()
        p.config = self.config
        return p


class ProjectCmd(_PrjCmd):
    """
    Commands to administer the storage repo of TA *projects*.

    A *project* stores all CO2MPAS files for a single vehicle,
    and tracks its sampling procedure.
    """

    examples = trt.Unicode("""
        To get the list with the status of all existing projects, try:

            co2dice project list
        """)


    class ListCmd(_PrjCmd):
        """
        List specified projects, or all, if none specified.

        - Use --verbose to view more infos about the projects, or use the `examine` cmd
          to view even more details for a specific project.

        SYNTAX
            co2dice project list [<project-1>] ...
        """
        def run(self, *args):
            self.log.info('Listing %s projects...', args or 'all')
            return self.projects_db.proj_list(*args)


    class CurrentCmd(_PrjCmd):
        """Prints the currently open project."""
        def run(self, *args):
            if len(args) != 0:
                raise baseapp.CmdException('Cmd %r takes no arguments, received %r!'
                                   % (self.name, args))
            return self.projects_db.current_project()


    class OpenCmd(_PrjCmd):
        """
        Make an existing project as *current*.

        SYNTAX
            co2dice project open <project>
        """
        def run(self, *args):
            self.log.info('Opening project %r...', args)
            if len(args) != 1:
                raise baseapp.CmdException("Cmd %r takes a SINGLE project-name as argument, received: %r!"
                                   % (self.name, args))
            return self.projects_db.proj_open(args[0])

    class AddCmd(_PrjCmd):
        """
        Create a new project.

        SYNTAX
            co2dice project add <project>
        """
        def run(self, *args):
            if len(args) != 1:
                raise baseapp.CmdException('Cmd %r takes a SINGLE project-name as argument, received %r!'
                                   % (self.name, args))
            return self.projects_db.proj_add(args[0])


    class AddReportCmd(_PrjCmd):
        """
        Import the specified input/output co2mpas files into the *current project*.

        The *report parameters* will be time-stamped and disseminated to
        TA authorities & oversight bodies with an email, to receive back
        the sampling decision.

        - One file from each kind (inp/out) may be given.
        - If an input/output is already present in the current project, use --force.

        SYNTAX
            co2dice project add-report ( inp=<co2mpas-file-1> | out=<co2mpas-file-1> ) ...
        """

        examples = trt.Unicode("""
            To import an INPUT co2mpas file, try:

                co2dice project add-report  inp=co2mpas_input.xlsx

            To import both INPUT and OUTPUT files, and overwrite any already imported try:

                co2dice project add-report --force inp=co2mpas_input.xlsx out=co2mpas_results.xlsx
            """)

        __report = None

        def run(self, *args):
            self.log.info('Importing report files %s...', args)
            if len(args) < 1:
                raise baseapp.CmdException('Cmd %r takes at least one argument, received %d: %r!'
                                   % (self.name, len(args), args))
            iofiles = report.parse_io_args(*args)
            if iofiles.other:
                raise baseapp.CmdException(
                    "Cmd %r filepaths must either start with 'inp=' or 'out=' prefix!\n%s"
                                       % (self.name, '\n'.join('  arg[%d]: %s' % i for i in iofiles.other.items())))

            return self.projects_db.current_project().import_iof(iofiles=iofiles)


    class ExamineCmd(_PrjCmd):
        """
        Print various information about the specified project, or the current-project, if none specified.

        - Use --verbose to view more infos, including about the repository as a whole.

        SYNTAX
            co2dice project examine [<project>]
        """
        as_json = trt.Bool(False,
                help="Whether to return infos as JSON, instead of python-code."
                ).tag(config=True)

        def run(self, *args):
            if len(args) > 1:
                raise baseapp.CmdException('Cmd %r takes one optional argument, received %d: %r!'
                                   % (self.name, len(args), args))
            pname = args and args[0] or None
            return self.projects_db.proj_examine(pname, as_text=True, as_json=self.as_json)


    class BackupCmd(_PrjCmd):
        """
        Backup projects repository into the archive filepath specified, or current-directory, if none specified.

        SYNTAX
            co2dice project backup [<archive-path>]
        """
        def run(self, *args):
            self.log.info('Archiving repo into %r...', args)
            if len(args) > 1:
                raise baseapp.CmdException('Cmd %r takes one optional argument, received %d: %r!'
                                   % (self.name, len(args), args))
            archive_fpath = args and args[0] or None
            kwds = {}
            if archive_fpath:
                base, fname = osp.split(archive_fpath)
                if base:
                    kwds['folder'] = base
                if fname:
                    kwds['repo_name'] = fname
            try:
                return self.projects_db.repo_backup(**kwds)
            except FileNotFoundError as ex:
                raise baseapp.CmdException("Folder '%s' to store archive does not exist!"
                                   "\n  Use --force to create it." % ex)


    def __init__(self, **kwds):
        with self.hold_trait_notifications():
            dkwds = {
                'conf_classes': [ProjectsDB, Project],
                'subcommands': baseapp.build_sub_cmds(*project_subcmds),
                #'default_subcmd': 'current', ## Does not help the user.
                'cmd_flags': {
                    'reset-git-settings': ({
                            'ProjectsDB': {'reset_settings': True},
                        }, first_line(ProjectsDB.reset_settings.help)),
                    'as-json': ({
                            'ExamineCmd': {'as_json': True},
                        }, first_line(ProjectCmd.ExamineCmd.as_json.help)),
                }
            }
            dkwds.update(kwds)
            super().__init__(**dkwds)

project_subcmds = (ProjectCmd.ListCmd, ProjectCmd.CurrentCmd, ProjectCmd.OpenCmd, ProjectCmd.AddCmd,
                   ProjectCmd.AddReportCmd, ProjectCmd.ExamineCmd, ProjectCmd.BackupCmd)

if __name__ == '__main__':
    from traitlets.config import get_config
    # Invoked from IDEs, so enable debug-logging.
    c = get_config()
    c.Application.log_level=0
    #c.Spec.log_level='ERROR'

    argv = None
    ## DEBUG AID ARGS, remember to delete them once developed.
    #argv = ''.split()
    #argv = '--debug'.split()

    dice.run_cmd(baseapp.chain_cmds(
        [dice.MainCmd, ProjectCmd, ProjectCmd.ListCmd],
        config=c))#argv=['project_foo']))
