#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

# Copyright (c) 2018 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
buildkit: A small helper utility for building ungoogled-chromium.

This is the CLI interface. Available commands each have their own help; pass in
-h or --help after a command.

buildkit has optional environment variables. They are as follows:

* BUILDKIT_RESOURCES - Path to the resources/ directory. Defaults to
the one in buildkit's parent directory.
* BUILDKIT_USER_BUNDLE - Path to the user config bundle. Without it, commands
 that need a bundle default to buildspace/user_bundle. This value can be
 overridden per-command with the --user-bundle option.
"""

import argparse
import os
from pathlib import Path

from . import config
from . import source_retrieval
from . import domain_substitution
from .common import (
    CONFIG_BUNDLES_DIR, BUILDSPACE_DOWNLOADS, BUILDSPACE_TREE,
    BUILDSPACE_TREE_PACKAGING, BUILDSPACE_USER_BUNDLE, SEVENZIP_USE_REGISTRY,
    BuildkitAbort, ExtractorEnum, get_resources_dir, get_logger)
from .config import ConfigBundle

# Classes

class _CLIError(RuntimeError):
    """Custom exception for printing argument parser errors from callbacks"""

def get_basebundle_verbosely(base_name):
    """
    Returns a ConfigBundle from the given base name, otherwise it logs errors and raises
    BuildkitAbort"""
    try:
        return ConfigBundle.from_base_name(base_name)
    except NotADirectoryError as exc:
        get_logger().error('resources/ or resources/patches directories could not be found.')
        raise BuildkitAbort()
    except FileNotFoundError:
        get_logger().error('The base config bundle "%s" does not exist.', base_name)
        raise BuildkitAbort()
    except ValueError as exc:
        get_logger().error('Base bundle metadata has an issue: %s', exc)
        raise BuildkitAbort()
    except BaseException:
        get_logger().exception('Unexpected exception caught.')
        raise BuildkitAbort()

class NewBaseBundleAction(argparse.Action): #pylint: disable=too-few-public-methods
    """argparse.ArgumentParser action handler with more verbose logging"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.type:
            raise ValueError('Cannot define action with action %s' % type(self).__name__)
        if self.nargs and self.nargs > 1:
            raise ValueError('nargs cannot be greater than 1')

    def __call__(self, parser, namespace, values, option_string=None):
        try:
            base_bundle = get_basebundle_verbosely(values)
        except BuildkitAbort:
            parser.exit(status=1)
        setattr(namespace, self.dest, base_bundle)

# Methods

def _default_user_bundle_path():
    """Returns the default path to the buildspace user bundle."""
    return os.getenv('BUILDKIT_USER_BUNDLE', default=BUILDSPACE_USER_BUNDLE)

def setup_bundle_group(parser):
    """Helper to add arguments for loading a config bundle to argparse.ArgumentParser"""
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        '-b', '--base-bundle', metavar='NAME', dest='bundle', default=argparse.SUPPRESS,
        action=NewBaseBundleAction,
        help=('The base config bundle name to use (located in resources/config_bundles). '
              'Mutually exclusive with --user-bundle. '
              'Default value is nothing; a user bundle is used by default'))
    config_group.add_argument(
        '-u', '--user-bundle', metavar='PATH', dest='bundle',
        default=_default_user_bundle_path(),
        type=lambda x: ConfigBundle(Path(x)),
        help=('The path to a user bundle to use. '
              'Mutually exclusive with --base-bundle. Use BUILDKIT_USER_BUNDLE '
              'to override the default value. Current default: %(default)s'))

def _add_bunnfo(subparsers):
    """Gets info about base bundles."""
    def _callback(args):
        if vars(args).get('list'):
            for bundle_dir in sorted(
                    (get_resources_dir() / CONFIG_BUNDLES_DIR).iterdir()):
                bundle_meta = config.BaseBundleMetaIni(
                    bundle_dir / config.BASEBUNDLEMETA_INI)
                print(bundle_dir.name, '-', bundle_meta.display_name)
        elif vars(args).get('bundle'):
            for dependency in args.bundle.get_dependencies():
                print(dependency)
        else:
            raise NotImplementedError()
    parser = subparsers.add_parser(
        'bunnfo', formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        help=_add_bunnfo.__doc__, description=_add_bunnfo.__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-l', '--list', action='store_true',
        help='Lists all base bundles and their display names.')
    group.add_argument(
        '-d', '--dependencies', dest='bundle',
        action=NewBaseBundleAction,
        help=('Prints the dependency order of the given base bundle, '
              'delimited by newline characters. '
              'See DESIGN.md for the definition of dependency order.'))
    parser.set_defaults(callback=_callback)

def _add_genbun(subparsers):
    """Generates a user config bundle from a base config bundle."""
    def _callback(args):
        try:
            args.base_bundle.write(args.user_bundle_path)
        except FileExistsError:
            get_logger().error('User bundle dir is not empty: %s', args.user_bundle_path)
            raise _CLIError()
        except ValueError as exc:
            get_logger().error('Error with base bundle: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'genbun', formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        help=_add_genbun.__doc__, description=_add_genbun.__doc__)
    parser.add_argument(
        '-u', '--user-bundle', metavar='PATH', dest='user_bundle_path',
        type=Path, default=_default_user_bundle_path(),
        help=('The output path for the user config bundle. '
              'The path must not already exist. '))
    parser.add_argument(
        'base_bundle', action=NewBaseBundleAction,
        help='The base config bundle name to use.')
    parser.set_defaults(callback=_callback)

def _add_getsrc(subparsers):
    """Downloads, checks, and unpacks the necessary files into the buildspace tree"""
    def _callback(args):
        try:
            extractors = {
                ExtractorEnum.SEVENZIP: args.sevenz_path,
                ExtractorEnum.TAR: args.tar_path,
            }
            source_retrieval.retrieve_and_extract(
                config_bundle=args.bundle, buildspace_downloads=args.downloads,
                buildspace_tree=args.tree, prune_binaries=args.prune_binaries,
                show_progress=args.show_progress, extractors=extractors,
                disable_ssl_verification=args.disable_ssl_verification)
        except FileExistsError as exc:
            get_logger().error('Directory is not empty: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error('Directory or file not found: %s', exc)
            raise _CLIError()
        except NotADirectoryError as exc:
            get_logger().error('Path is not a directory: %s', exc)
            raise _CLIError()
        except source_retrieval.NotAFileError as exc:
            get_logger().error('Archive path is not a regular file: %s', exc)
            raise _CLIError()
        except source_retrieval.HashMismatchError as exc:
            get_logger().error('Archive checksum is invalid: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'getsrc', help=_add_getsrc.__doc__ + '.',
        description=_add_getsrc.__doc__ + '; ' + (
            'these are the Chromium source code and any extra dependencies. '
            'By default, binary pruning is performed during extraction. '
            'The %s directory must already exist for storing downloads. '
            'If the buildspace tree already exists or there is a checksum mismatch, '
            'this command will abort. '
            'Only files that are missing will be downloaded. '
            'If the files are already downloaded, their checksums are '
            'confirmed and then they are unpacked.') % BUILDSPACE_DOWNLOADS)
    setup_bundle_group(parser)
    parser.add_argument(
        '-t', '--tree', type=Path, default=BUILDSPACE_TREE,
        help='The buildspace tree path. Default: %(default)s')
    parser.add_argument(
        '-d', '--downloads', type=Path, default=BUILDSPACE_DOWNLOADS,
        help=('Path to store archives of Chromium source code and extra deps. '
              'Default: %(default)s'))
    parser.add_argument(
        '--disable-binary-pruning', action='store_false', dest='prune_binaries',
        help='Disables binary pruning during extraction.')
    parser.add_argument(
        '--hide-progress-bar', action='store_false', dest='show_progress',
        help='Hide the download progress.')
    parser.add_argument(
        '--tar-path', default='tar',
        help=('(Linux and macOS only) Command or path to the BSD or GNU tar '
              'binary for extraction. Default: %(default)s'))
    parser.add_argument(
        '--7z-path', dest='sevenz_path', default=SEVENZIP_USE_REGISTRY,
        help=('Command or path to 7-Zip\'s "7z" binary. If "_use_registry" is '
              'specified, determine the path from the registry. Default: %(default)s'))
    parser.add_argument(
        '--disable-ssl-verification', action='store_true',
        help='Disables certification verification for downloads using HTTPS.')
    parser.set_defaults(callback=_callback)

def _add_prubin(subparsers):
    """Prunes binaries from the buildspace tree."""
    def _callback(args):
        logger = get_logger()
        try:
            resolved_tree = args.tree.resolve()
        except FileNotFoundError as exc:
            logger.error('File or directory does not exist: %s', exc)
            raise _CLIError()
        missing_file = False
        for tree_node in args.bundle.pruning:
            try:
                (resolved_tree / tree_node).unlink()
            except FileNotFoundError:
                missing_file = True
                logger.warning('No such file: %s', resolved_tree / tree_node)
        if missing_file:
            raise _CLIError()
    parser = subparsers.add_parser(
        'prubin', help=_add_prubin.__doc__, description=_add_prubin.__doc__ + (
            ' This is NOT necessary if the source code was already pruned '
            'during the getsrc command.'))
    setup_bundle_group(parser)
    parser.add_argument(
        '-t', '--tree', type=Path, default=BUILDSPACE_TREE,
        help='The buildspace tree path to apply binary pruning. Default: %(default)s')
    parser.set_defaults(callback=_callback)

def _add_subdom(subparsers):
    """Substitutes domain names in buildspace tree or patches with blockable strings."""
    def _callback(args):
        try:
            if not args.only or args.only == 'tree':
                domain_substitution.process_tree_with_bundle(args.bundle, args.tree)
            if not args.only or args.only == 'patches':
                domain_substitution.process_bundle_patches(args.bundle)
        except FileNotFoundError as exc:
            get_logger().error('File or directory does not exist: %s', exc)
            raise _CLIError()
        except NotADirectoryError as exc:
            get_logger().error('Patches directory does not exist: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'subdom', help=_add_subdom.__doc__, description=_add_subdom.__doc__ + (
            ' By default, it will substitute the domains on both the buildspace tree and '
            'the bundle\'s patches.'))
    setup_bundle_group(parser)
    parser.add_argument(
        '-o', '--only', choices=['tree', 'patches'],
        help=('Specifies a component to exclusively apply domain substitution to. '
              '"tree" is for the buildspace tree, and "patches" is for the bundle\'s patches.'))
    parser.add_argument(
        '-t', '--tree', type=Path, default=BUILDSPACE_TREE,
        help=('The buildspace tree path to apply domain substitution. '
              'Not applicable when --only is "patches". Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg_archlinux(subparsers):
    """Generates a PKGBUILD for Arch Linux"""
    def _callback(args):
        from .packaging import archlinux as packaging_archlinux
        try:
            packaging_archlinux.generate_packaging(
                args.bundle, args.output, repo_version=args.repo_commit,
                repo_hash=args.repo_hash)
        except FileExistsError as exc:
            get_logger().error('PKGBUILD already exists: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error(
                'Output path is not an existing directory: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'archlinux', help=_add_genpkg_archlinux.__doc__,
        description=_add_genpkg_archlinux.__doc__)
    parser.add_argument(
        '-o', '--output', type=Path, default='buildspace',
        help=('The directory to store packaging files. '
              'It must exist and not already contain a PKGBUILD file. '
              'Default: %(default)s'))
    parser.add_argument(
        '--repo-commit', action='store_const', const='git', default='bundle',
        help=("Use the current git repo's commit hash to specify the "
              "ungoogled-chromium repo to download instead of a tag determined "
              "by the config bundle's version config file. Requires git to be "
              "in PATH and buildkit to be invoked inside of a clone of "
              "ungoogled-chromium's git repository."))
    parser.add_argument(
        '--repo-hash', default='SKIP',
        help=('The SHA-256 hash to verify the archive of the ungoogled-chromium '
              'repository to download within the PKGBUILD. If it is "compute", '
              'the hash is computed by downloading the archive to memory and '
              'computing the hash. If it is "SKIP", hash computation is skipped. '
              'Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg_debian(subparsers):
    """Generate Debian packaging files"""
    def _callback(args):
        from .packaging import debian as packaging_debian
        try:
            packaging_debian.generate_packaging(args.bundle, args.flavor, args.output)
        except FileExistsError as exc:
            get_logger().error('debian directory is not empty: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error(
                'Parent directories do not exist for path: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'debian', help=_add_genpkg_debian.__doc__, description=_add_genpkg_debian.__doc__)
    parser.add_argument(
        '-f', '--flavor', required=True, help='The Debian packaging flavor to use.')
    parser.add_argument(
        '-o', '--output', type=Path, default='%s/debian' % BUILDSPACE_TREE,
        help=('The path to the debian directory to be created. '
              'It must not already exist, but the parent directories must exist. '
              'Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg_linux_simple(subparsers):
    """Generate Linux Simple packaging files"""
    def _callback(args):
        from .packaging import linux_simple as packaging_linux_simple
        try:
            packaging_linux_simple.generate_packaging(args.bundle, args.output)
        except FileExistsError as exc:
            get_logger().error('Output directory is not empty: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error(
                'Parent directories do not exist for path: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'linux_simple', help=_add_genpkg_linux_simple.__doc__,
        description=_add_genpkg_linux_simple.__doc__)
    parser.add_argument(
        '-o', '--output', type=Path, default=BUILDSPACE_TREE_PACKAGING,
        help=('The directory to store packaging files. '
              'It must not already exist, but the parent directories must exist. '
              'Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg_opensuse(subparsers):
    """Generate OpenSUSE packaging files"""
    def _callback(args):
        from .packaging import opensuse as packaging_opensuse
        try:
            packaging_opensuse.generate_packaging(args.bundle, args.output)
        except FileExistsError as exc:
            get_logger().error('Output directory is not empty: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error(
                'Parent directories do not exist for path: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'opensuse', help=_add_genpkg_opensuse.__doc__,
        description=_add_genpkg_opensuse.__doc__)
    parser.add_argument(
        '-o', '--output', type=Path, default=BUILDSPACE_TREE_PACKAGING,
        help=('The directory to store packaging files. '
              'It must not already exist, but the parent directories must exist. '
              'Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg_windows(subparsers):
    """Generate Microsoft Windows packaging files"""
    def _callback(args):
        from .packaging import windows as packaging_windows
        try:
            packaging_windows.generate_packaging(args.bundle, args.output)
        except FileExistsError as exc:
            get_logger().error('Output directory is not empty: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error(
                'Parent directories do not exist for path: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'windows', help=_add_genpkg_windows.__doc__,
        description=_add_genpkg_windows.__doc__)
    parser.add_argument(
        '-o', '--output', type=Path, default=BUILDSPACE_TREE_PACKAGING,
        help=('The directory to store packaging files. '
              'It must not already exist, but the parent directories must exist. '
              'Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg_macos(subparsers):
    """Generate macOS packaging files"""
    def _callback(args):
        from .packaging import macos as packaging_macos
        try:
            packaging_macos.generate_packaging(args.bundle, args.output)
        except FileExistsError as exc:
            get_logger().error('Output directory is not empty: %s', exc)
            raise _CLIError()
        except FileNotFoundError as exc:
            get_logger().error(
                'Parent directories do not exist for path: %s', exc)
            raise _CLIError()
    parser = subparsers.add_parser(
        'macos', help=_add_genpkg_macos.__doc__, description=_add_genpkg_macos.__doc__)
    parser.add_argument(
        '-o', '--output', type=Path, default=BUILDSPACE_TREE_PACKAGING,
        help=('The directory to store packaging files. '
              'It must not already exist, but the parent directories must exist. '
              'Default: %(default)s'))
    parser.set_defaults(callback=_callback)

def _add_genpkg(subparsers):
    """Generates a packaging script."""
    parser = subparsers.add_parser(
        'genpkg', help=_add_genpkg.__doc__,
        description=_add_genpkg.__doc__ + ' Specify no arguments to get a list of different types.')
    setup_bundle_group(parser)
    # Add subcommands to genpkg for handling different packaging types in the same manner as main()
    # However, the top-level argparse.ArgumentParser will be passed the callback.
    subsubparsers = parser.add_subparsers(title='Available packaging types', dest='packaging')
    subsubparsers.required = True # Workaround for http://bugs.python.org/issue9253#msg186387
    _add_genpkg_archlinux(subsubparsers)
    _add_genpkg_debian(subsubparsers)
    _add_genpkg_linux_simple(subsubparsers)
    _add_genpkg_opensuse(subsubparsers)
    _add_genpkg_windows(subsubparsers)
    _add_genpkg_macos(subsubparsers)

def main(arg_list=None):
    """CLI entry point"""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)

    subparsers = parser.add_subparsers(title='Available commands', dest='command')
    subparsers.required = True # Workaround for http://bugs.python.org/issue9253#msg186387
    _add_bunnfo(subparsers)
    _add_genbun(subparsers)
    _add_getsrc(subparsers)
    _add_prubin(subparsers)
    _add_subdom(subparsers)
    _add_genpkg(subparsers)

    args = parser.parse_args(args=arg_list)
    try:
        args.callback(args=args)
    except (_CLIError, BuildkitAbort):
        parser.exit(status=1)
    except BaseException:
        get_logger().exception('Unexpected exception caught.')
        parser.exit(status=1)
