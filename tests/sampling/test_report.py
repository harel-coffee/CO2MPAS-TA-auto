#! python
# -*- coding: UTF-8 -*-
#
# Copyright 2015-2016 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

import logging
import re
import tempfile
import types
import unittest

import ddt
from traitlets.config import get_config

from co2mpas.__main__ import init_logging
from co2mpas.sampling import CmdException, report, project
from tests.sampling import test_inp_fpath, test_out_fpath, test_vfid
import os.path as osp
import pandas as pd


init_logging(level=logging.DEBUG)

log = logging.getLogger(__name__)

mydir = osp.dirname(__file__)


@ddt.ddt
class TApp(unittest.TestCase):

    @ddt.data(
        report.ReportCmd.document_config_options,
        report.ReportCmd.print_alias_help,
        report.ReportCmd.print_flag_help,
        report.ReportCmd.print_options,
        report.ReportCmd.print_subcommands,
        report.ReportCmd.print_examples,
        report.ReportCmd.print_help,
    )
    def test_app(self, meth):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        cmd = report.ReportCmd(config=c)
        meth(cmd)


class TReportArgs(unittest.TestCase):

    def check_report_tuple(self, k, vfid, fpath, iokind, dice_report=None):
        self.assertEqual(len(k), 4)
        self.assertEqual(k[0], vfid)
        self.assertTrue(k[1].endswith(osp.basename(fpath)))
        self.assertEqual(k[2], iokind)
        dr = k[3]
        if dice_report is None:
            self.assertIsNone(dr)
        elif dice_report is True:
            self.assertIsInstance(dr, str)
        else:
            self.assertEqual(dr, dice_report)

    def test_extract_input(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        cmd = report.ReportCmd(config=c)
        res = cmd.run('inp=%s' % test_inp_fpath)
        self.assertIsInstance(res, types.GeneratorType)
        res = list(res)
        self.assertEqual(len(res), 1)
        self.check_report_tuple(res[0], test_vfid, test_inp_fpath, 'inp')

    def test_extract_output(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        cmd = report.ReportCmd(config=c)
        res = cmd.run('out=%s' % test_out_fpath)
        self.assertIsInstance(res, types.GeneratorType)
        res = list(res)
        self.assertEqual(len(res), 1)
        self.check_report_tuple(res[0], test_vfid, test_out_fpath, 'out', True)

    def test_extract_both(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        cmd = report.ReportCmd(config=c)
        res = cmd.run('inp=%s' % test_inp_fpath, 'out=%s' % test_out_fpath)
        self.assertIsInstance(res, types.GeneratorType)
        res = list(res)
        self.assertEqual(len(res), 2)
        self.check_report_tuple(res[0], test_vfid, test_inp_fpath, 'inp')
        self.check_report_tuple(res[1], test_vfid, test_out_fpath, 'out', True)

    def test_bad_prefix(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        cmd = report.ReportCmd(config=c)

        arg = 'BAD_ARG'
        with self.assertRaisesRegexp(CmdException, re.escape("arg[1]: %s" % arg)):
            list(cmd.run(arg))

        arg = 'inp:BAD_ARG'
        with self.assertRaisesRegexp(CmdException, re.escape("arg[1]: %s" % arg)):
            list(cmd.run(arg))

        arg1 = 'inp:FOO'
        arg2 = 'out.BAR'
        with self.assertRaises(CmdException) as cm:
            list(cmd.run('inp=A', arg1, 'out=B', arg2))
        #print(cm.exception)
        self.assertIn("arg[2]: %s" % arg1, str(cm.exception))
        self.assertIn("arg[4]: %s" % arg2, str(cm.exception))


class TReportProject(unittest.TestCase):
    def test_fails_with_args(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        c.ReportCmd.project = True
        with self.assertRaisesRegex(CmdException, "--project' takes no arguments, received"):
            list(report.ReportCmd(config=c).run('EXTRA_ARG'))

    def test_fails_when_no_project(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        c.ReportCmd.project = True
        with tempfile.TemporaryDirectory() as td:
            c.ProjectsDB.repo_path = td
            cmd = report.ReportCmd(config=c)
            with self.assertRaisesRegex(CmdException, r"No current-project exists yet!"):
                list(cmd.run())

    def test_fails_when_empty(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        c.ReportCmd.project = True
        with tempfile.TemporaryDirectory() as td:
            c.ProjectsDB.repo_path = td
            project.ProjectCmd.InitCmd(config=c).run('proj1')
            cmd = report.ReportCmd(config=c)
            with self.assertRaisesRegex(
                CmdException, re.escape(
                    r"Current Project(proj1: empty) contains no input/output files!")):
                list(cmd.run())

    def test_input_output(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        c.ReportCmd.project = True
        with tempfile.TemporaryDirectory() as td:
            c.ProjectsDB.repo_path = td
            project.ProjectCmd.InitCmd(config=c).run('proj1')

            project.ProjectCmd.AddFileCmd(config=c).run('inp=%s' % test_inp_fpath)
            cmd = report.ReportCmd(config=c)
            res = cmd.run()
            self.assertIsInstance(res, types.GeneratorType)
            res = list(res)
            self.assertEqual(len(res), 0)
            for i in res:
                self.assertIsInstance(i, pd.Series)

            project.ProjectCmd.AddFileCmd(config=c).run('out=%s' % test_out_fpath)
            cmd = report.ReportCmd(config=c)
            res = cmd.run()
            self.assertIsInstance(res, types.GeneratorType)
            res = list(res)
            self.assertEqual(len(res), 1)

    def test_output_input(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        c.ReportCmd.project = True
        with tempfile.TemporaryDirectory() as td:
            c.ProjectsDB.repo_path = td
            project.ProjectCmd.InitCmd(config=c).run('proj1')

            project.ProjectCmd.AddFileCmd(config=c).run('out=%s' % test_out_fpath)
            cmd = report.ReportCmd(config=c)
            res = cmd.run()
            self.assertIsInstance(res, types.GeneratorType)
            res = list(res)
            self.assertEqual(len(res), 1)
            for i in res:
                self.assertIsInstance(i, pd.DataFrame)

            project.ProjectCmd.AddFileCmd(config=c).run('inp=%s' % test_inp_fpath)
            cmd = report.ReportCmd(config=c)
            res = cmd.run()
            self.assertIsInstance(res, types.GeneratorType)
            res = list(res)
            self.assertEqual(len(res), 1)
            for i in res:
                self.assertIsInstance(i, pd.DataFrame)

    def test_both(self):
        c = get_config()
        c.ReportCmd.raise_config_file_errors = True
        c.ReportCmd.project = True
        with tempfile.TemporaryDirectory() as td:
            c.ProjectsDB.repo_path = td
            project.ProjectCmd.InitCmd(config=c).run('proj1')

            cmd = project.ProjectCmd.AddFileCmd(config=c)
            cmd.run('out=%s' % test_out_fpath, 'inp=%s' % test_inp_fpath)
            cmd = report.ReportCmd(config=c)
            res = cmd.run()
            self.assertIsInstance(res, types.GeneratorType)
            res = list(res)
            self.assertEqual(len(res), 1)
            for i in res:
                self.assertIsInstance(i, pd.DataFrame)
