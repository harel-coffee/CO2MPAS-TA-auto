#! python
# -*- coding: UTF-8 -*-
#
# Copyright 2015-2019 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

import ddt
import filecmp
import unittest
import os.path as osp

cdir = osp.abspath(osp.dirname(__file__))
fdir = osp.join(cdir, 'files')
pdir = osp.abspath(osp.join(cdir, '../co2mpas'))


@ddt.ddt
class CLI(unittest.TestCase):
    def setUp(self):
        import functools
        from co2mpas.cli import cli
        from click.testing import CliRunner
        self.runner = CliRunner()
        self.invoke = functools.partial(self.runner.invoke, cli)

    @ddt.idata((
            (),
            ('temp.xlsx',),
            ('folder/temp.xlsx',),
            ('folder/temp.xlsx', '-TT', 'input'),
            ('folder/temp.xlsx', '-TT', 'output')
    ))
    def test_template(self, options):
        from co2mpas.cli import template
        kw = template.make_context('template', list(options)).params
        rfp = osp.join(pdir, 'templates/%s_template.xlsx' % kw['template_type'])
        with self.runner.isolated_filesystem():
            result = self.invoke(('template',) + options)
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(
                filecmp.cmp(kw['output_file'], rfp),
                'Template file is not as expected!'
            )

    @ddt.idata((
            (),
            ('.',),
            ('demos',),
            ('demo/inputs',)
    ))
    def test_demo(self, options):
        import glob
        from co2mpas.cli import demo
        kw = demo.make_context('demo', list(options)).params
        d_demo = osp.join(pdir, 'demos/*.xlsx')
        demos = {osp.basename(fp): fp for fp in glob.glob(d_demo)}
        with self.runner.isolated_filesystem():
            result = self.invoke(('demo',) + options)
            self.assertEqual(result.exit_code, 0)
            for fpath in glob.glob(osp.join(kw['output_folder'], '*.xlsx')):
                self.assertTrue(
                    filecmp.cmp(fpath, demos[osp.basename(fpath)]),
                    'Demo file (%s) is not as expected!' % fpath
                )

    @ddt.idata((
            (),
            ('temp.yaml',),
            ('conf/temp.yaml',),
            ('conf/temp.yaml', '-MC', osp.join(fdir, 'conf.yaml'))
    ))
    def test_conf(self, options):
        import yaml
        import schedula as sh
        from co2mpas.core.model.physical.defaults import dfl
        from co2mpas.cli import conf
        kw = conf.make_context('conf', list(options)).params
        t = {k for k, _ in sh.stack_nested_keys(dfl.to_dict())}
        with self.runner.isolated_filesystem():
            result = self.invoke(('conf',) + options)
            self.assertEqual(result.exit_code, 0)
            with open(kw['output_file'], 'rb') as f:
                r = dict(sh.stack_nested_keys(yaml.load(f)))
                self.assertSetEqual(set(r), t)
            if kw['model_conf']:
                with open(kw['model_conf'], 'rb') as f:
                    for k, v in sh.stack_nested_keys(yaml.load(f)):
                        self.assertEqual(r[k], v)

    @ddt.idata((
            (osp.join(fdir, 'input.co2mpas.ta'), osp.join(pdir, 'demos'),
             '-EK', osp.join(fdir, 'keys/secret.co2mpas.keys'),
             '-KP', osp.join(fdir, 'keys/secret.passwords'), '-OS'),
            (osp.join(pdir, 'demos', 'co2mpas_demo-1.xlsx'), '-TA',
             '-EK', osp.join(fdir, 'keys/dice.co2mpas.keys')),
    ))
    def test_run(self, options):
        import glob
        import pandas as pd
        from co2mpas.cli import run
        kw = run.make_context('run', list(options)).params
        with self.runner.isolated_filesystem():
            result = self.invoke(('run',) + options)
            self.assertEqual(result.exit_code, 0)
            cols = 'declared_value', 'prediction', 'output'
            df = pd.read_excel(
                glob.glob(osp.join(kw['output_folder'], '*-summary.xlsx'))[0],
                header=[0, 1, 2, 3, 4], index_col=[0, 1]
            ).droplevel(-1).droplevel(-1, 1).swaplevel(0, -1, 1)[cols].T
            cycles = {
                tuple(map('{}-co2mpas_simplan'.format, (
                    'invert_cycles', 'rts_cycle', 'manual', 'hot',
                    'alternator_efficiency', 'lights', 'biofuel', 'slope',
                    'cylinder_deactivation', 'road'
                ))) + ('change_base-co2mpas_demo-1',): {'wltp_h', 'wltp_l'},
                ('co2mpas_demo-1', 'co2mpas_simplan', 'input.co2mpas') + tuple(
                    map('{}-co2mpas_simplan'.format, ('nedc_cycle', 'tyre'))
                ): {'wltp_h', 'wltp_l', 'nedc_h', 'nedc_l'}
            }

            for k, v in df.items():
                v = v.dropna().to_dict()
                if k == 'input.co2mpas' and 'co2mpas_demo-1' in df:
                    self.assertEqual(v, df['co2mpas_demo-1'].dropna().to_dict())
                for i, c in cycles.items():  # predicted cycles.
                    if k in i:
                        self.assertSetEqual(c, set(v))
                        break
                else:
                    raise ValueError(f'{k} is not contemplated!')
