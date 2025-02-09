#!/usr/bin/env python3

# Copyright 2022 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Generate experiment related code artifacts.

Invoke as: tools/codegen/core/gen_experiments.py
Experiment definitions are in src/core/lib/experiments/experiments.yaml
"""

from __future__ import print_function

import collections
import ctypes
import datetime
import json
import math
import os
import re
import sys

import yaml

# TODO(ctiller): if we ever add another argument switch this to argparse
check_dates = True
if sys.argv[1:] == ["--check"]:
    check_dates = False  # for formatting checks we don't verify expiry dates

with open('src/core/lib/experiments/experiments.yaml') as f:
    attrs = yaml.safe_load(f.read())

with open('src/core/lib/experiments/rollouts.yaml') as f:
    rollouts = yaml.safe_load(f.read())

DEFAULTS = {
    'broken': 'false',
    False: 'false',
    True: 'true',
    'debug': 'kDefaultForDebugOnly',
}

FINAL_RETURN = {
    'broken': 'return false;',
    False: 'return false;',
    True: 'return true;',
    'debug': '#ifdef NDEBUG\nreturn false;\n#else\nreturn true;\n#endif',
}

FINAL_DEFINE = {
    'broken': None,
    False: None,
    True: '#define %s',
    'debug': '#ifndef NDEBUG\n#define %s\n#endif',
}

BZL_LIST_FOR_DEFAULTS = {
    'broken': None,
    False: 'off',
    True: 'on',
    'debug': 'dbg',
}

error = False
today = datetime.date.today()
two_quarters_from_now = today + datetime.timedelta(days=180)
experiment_annotation = 'gRPC experiments:'
for rollout_attr in rollouts:
    if 'name' not in rollout_attr:
        print("experiment with no name: %r" % attr)
        error = True
        continue
    if 'default' not in rollout_attr:
        print("no default for experiment %s" % rollout_attr['name'])
        error = True
    if rollout_attr['default'] not in DEFAULTS:
        print("invalid default for experiment %s: %r" %
              (rollout_attr['name'], rollout_attr['default']))
        error = True
for attr in attrs:
    if 'name' not in attr:
        print("experiment with no name: %r" % attr)
        error = True
        continue  # can't run other diagnostics because we don't know a name
    if 'description' not in attr:
        print("no description for experiment %s" % attr['name'])
        error = True
    if 'owner' not in attr:
        print("no owner for experiment %s" % attr['name'])
        error = True
    if 'expiry' not in attr:
        print("no expiry for experiment %s" % attr['name'])
        error = True
    if attr['name'] == 'monitoring_experiment':
        if attr['expiry'] != 'never-ever':
            print("monitoring_experiment should never expire")
            error = True
    else:
        expiry = datetime.datetime.strptime(attr['expiry'], '%Y/%m/%d').date()
        if check_dates:
            if expiry < today:
                print("experiment %s expired on %s" %
                      (attr['name'], attr['expiry']))
                error = True
            if expiry > two_quarters_from_now:
                print("experiment %s expires far in the future on %s" %
                      (attr['name'], attr['expiry']))
                print("expiry should be no more than two quarters from now")
                error = True
            experiment_annotation += attr['name'] + ':0,'

if len(experiment_annotation) > 2000:
    print("comma-delimited string of experiments is too long")
    error = True

if error:
    sys.exit(1)


def c_str(s, encoding='ascii'):
    if isinstance(s, str):
        s = s.encode(encoding)
    result = ''
    for c in s:
        c = chr(c) if isinstance(c, int) else c
        if not (32 <= ord(c) < 127) or c in ('\\', '"'):
            result += '\\%03o' % ord(c)
        else:
            result += c
    return '"' + result + '"'


def snake_to_pascal(s):
    return ''.join(x.capitalize() for x in s.split('_'))


# utility: print a big comment block into a set of files
def put_banner(files, banner, prefix):
    for f in files:
        for line in banner:
            if not line:
                print(prefix, file=f)
            else:
                print('%s %s' % (prefix, line), file=f)
        print(file=f)


def put_copyright(file, prefix):
    # copy-paste copyright notice from this file
    with open(sys.argv[0]) as my_source:
        copyright = []
        for line in my_source:
            if line[0] != '#':
                break
        for line in my_source:
            if line[0] == '#':
                copyright.append(line)
                break
        for line in my_source:
            if line[0] != '#':
                break
            copyright.append(line)
        put_banner([file], [line[2:].rstrip() for line in copyright], prefix)


def get_rollout_attr_for_experiment(name):
    for rollout_attr in rollouts:
        if rollout_attr['name'] == name:
            return rollout_attr
    print('WARNING. experiment: %r has no rollout config. Disabling it.' % name)
    return {'name': name, 'default': 'false'}


WTF = """
This file contains the autogenerated parts of the experiments API.

It generates two symbols for each experiment.

For the experiment named new_car_project, it generates:

- a function IsNewCarProjectEnabled() that returns true if the experiment
  should be enabled at runtime.

- a macro GRPC_EXPERIMENT_IS_INCLUDED_NEW_CAR_PROJECT that is defined if the
  experiment *could* be enabled at runtime.

The function is used to determine whether to run the experiment or
non-experiment code path.

If the experiment brings significant bloat, the macro can be used to avoid
including the experiment code path in the binary for binaries that are size
sensitive.

By default that includes our iOS and Android builds.

Finally, a small array is included that contains the metadata for each
experiment.

A macro, GRPC_EXPERIMENTS_ARE_FINAL, controls whether we fix experiment
configuration at build time (if it's defined) or allow it to be tuned at
runtime (if it's disabled).

If you are using the Bazel build system, that macro can be configured with
--define=grpc_experiments_are_final=true
"""

with open('src/core/lib/experiments/experiments.h', 'w') as H:
    put_copyright(H, "//")

    put_banner(
        [H],
        ["Automatically generated by tools/codegen/core/gen_experiments.py"] +
        WTF.splitlines(), "//")

    print("#ifndef GRPC_SRC_CORE_LIB_EXPERIMENTS_EXPERIMENTS_H", file=H)
    print("#define GRPC_SRC_CORE_LIB_EXPERIMENTS_EXPERIMENTS_H", file=H)
    print(file=H)
    print("#include <grpc/support/port_platform.h>", file=H)
    print(file=H)
    print("#include <stddef.h>", file=H)
    print("#include \"src/core/lib/experiments/config.h\"", file=H)
    print(file=H)
    print("namespace grpc_core {", file=H)
    print(file=H)
    print("#ifdef GRPC_EXPERIMENTS_ARE_FINAL", file=H)
    for i, attr in enumerate(attrs):
        rollout_attr = get_rollout_attr_for_experiment(attr['name'])
        define_fmt = FINAL_DEFINE[rollout_attr['default']]
        if define_fmt:
            print(define_fmt %
                  ("GRPC_EXPERIMENT_IS_INCLUDED_%s" % attr['name'].upper()),
                  file=H)
        print("inline bool Is%sEnabled() { %s }" % (snake_to_pascal(
            attr['name']), FINAL_RETURN[rollout_attr['default']]),
              file=H)
    print("#else", file=H)
    for i, attr in enumerate(attrs):
        print("#define GRPC_EXPERIMENT_IS_INCLUDED_%s" % attr['name'].upper(),
              file=H)
        print("inline bool Is%sEnabled() { return IsExperimentEnabled(%d); }" %
              (snake_to_pascal(attr['name']), i),
              file=H)
    print(file=H)
    print("constexpr const size_t kNumExperiments = %d;" % len(attrs), file=H)
    print(
        "extern const ExperimentMetadata g_experiment_metadata[kNumExperiments];",
        file=H)
    print(file=H)
    print("#endif", file=H)
    print("}  // namespace grpc_core", file=H)
    print(file=H)
    print("#endif  // GRPC_SRC_CORE_LIB_EXPERIMENTS_EXPERIMENTS_H", file=H)

with open('src/core/lib/experiments/experiments.cc', 'w') as C:
    put_copyright(C, "//")

    put_banner(
        [C],
        ["Automatically generated by tools/codegen/core/gen_experiments.py"],
        "//")

    print("#include <grpc/support/port_platform.h>", file=C)
    print("#include \"src/core/lib/experiments/experiments.h\"", file=C)
    print(file=C)
    print("#ifndef GRPC_EXPERIMENTS_ARE_FINAL", file=C)
    print("namespace {", file=C)
    for attr in attrs:
        print("const char* const description_%s = %s;" %
              (attr['name'], c_str(attr['description'])),
              file=C)
        print("const char* const additional_constraints_%s = \"\";" %
              attr['name'],
              file=C)
    have_defaults = set(
        DEFAULTS[rollout_attr['default']] for rollout_attr in rollouts)
    if 'kDefaultForDebugOnly' in have_defaults:
        print("#ifdef NDEBUG", file=C)
        if 'kDefaultForDebugOnly' in have_defaults:
            print("const bool kDefaultForDebugOnly = false;", file=C)
        print("#else", file=C)
        if 'kDefaultForDebugOnly' in have_defaults:
            print("const bool kDefaultForDebugOnly = true;", file=C)
        print("#endif", file=C)
    print("}", file=C)
    print(file=C)
    print("namespace grpc_core {", file=C)
    print(file=C)
    print("const ExperimentMetadata g_experiment_metadata[] = {", file=C)
    for attr in attrs:
        rollout_attr = get_rollout_attr_for_experiment(attr['name'])
        print(
            "  {%s, description_%s, additional_constraints_%s, %s, %s}," %
            (c_str(attr['name']), attr['name'], attr['name'],
             DEFAULTS[rollout_attr['default']],
             'true' if attr.get('allow_in_fuzzing_config', True) else 'false'),
            file=C)
    print("};", file=C)
    print(file=C)
    print("}  // namespace grpc_core", file=C)
    print("#endif", file=C)

bzl_to_tags_to_experiments = dict((key, collections.defaultdict(list))
                                  for key in BZL_LIST_FOR_DEFAULTS.keys()
                                  if key is not None)

for attr in attrs:
    rollout_attr = get_rollout_attr_for_experiment(attr['name'])
    for tag in attr['test_tags']:
        bzl_to_tags_to_experiments[rollout_attr['default']][tag].append(
            attr['name'])

with open('bazel/experiments.bzl', 'w') as B:
    put_copyright(B, "#")

    put_banner(
        [B],
        ["Automatically generated by tools/codegen/core/gen_experiments.py"],
        "#")

    print(
        "\"\"\"Dictionary of tags to experiments so we know when to test different experiments.\"\"\"",
        file=B)

    bzl_to_tags_to_experiments = sorted(
        (BZL_LIST_FOR_DEFAULTS[default], tags_to_experiments)
        for default, tags_to_experiments in bzl_to_tags_to_experiments.items()
        if BZL_LIST_FOR_DEFAULTS[default] is not None)

    print(file=B)
    print("EXPERIMENTS = {", file=B)
    for key, tags_to_experiments in bzl_to_tags_to_experiments:
        print("    \"%s\": {" % key, file=B)
        for tag, experiments in sorted(tags_to_experiments.items()):
            print("        \"%s\": [" % tag, file=B)
            for experiment in sorted(experiments):
                print("            \"%s\"," % experiment, file=B)
            print("        ],", file=B)
        print("    },", file=B)
    print("}", file=B)
