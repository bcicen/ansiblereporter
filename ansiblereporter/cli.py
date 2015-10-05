"""
Script wrappers for ansible commands and PlaybookScript

Wrap calling of ansible commands and playbooks to a script, extending
systematic.shell.Script classes.
"""

import os
import threading
import getpass

from systematic.shell import Script
from systematic.log import Logger

from ansible import __version__ as ansible_version
from ansible import utils
from ansible.constants import DEFAULT_MODULE_NAME, DEFAULT_MODULE_PATH, DEFAULT_MODULE_ARGS, \
                              DEFAULT_TIMEOUT, DEFAULT_HOST_LIST, DEFAULT_PRIVATE_KEY_FILE, \
                              DEFAULT_FORKS, DEFAULT_REMOTE_PORT, DEFAULT_PATTERN, \
                              DEFAULT_BECOME_USER, DEFAULT_HOST_LIST, active_user

from ansible.errors import AnsibleError
from ansible.inventory import Inventory

from ansiblereporter import RunnerError
from ansiblereporter import __version__ as ansible_reporter_version
from ansiblereporter.result import PlaybookRunner, AnsibleRunner


logger = Logger().default_stream


def create_directory(directory):
    """Create directory

    Wrapper to attempt creating directory unless it exists.

    Raises RunnerError if any errors happen.
    """
    if os.path.isdir(directory):
        logger.debug('directory already exists: %s' % directory)
        return

    try:
        os.makedirs(directory)
    except IOError, (ecode, emsg):
        raise RunnerError('Error creating directory %s: %s' % (directory, emsg))
    except OSError, (ecode, emsg):
        raise RunnerError('Error creating directory %s: %s' % (directory, emsg))


class GenericAnsibleScript(Script):
    """Ansible script wrapper base class

    Extend systematic.shell.Script (which wraps argparse.ArgumentParser) to run ansible
    and playbook commands. This is just the base class for variants.
    """

    def __init__(self, *args, **kwargs):
        Script.__init__(self, *args, **kwargs)
        self.runner = None
        self.mode = ''
        self.add_argument('--version', action='store_true', help='show version')

    def SIGINT(self, signum, frame):
        """
        Parse SIGINT signal by quitting the program cleanly with exit code 1
        """
        if self.runner is not None:
            raise KeyboardInterrupt()
        else:
            self.exit(1)

    def show_version(self):
        self.message('{0} (ansible {1})'.format(
            ansible_reporter_version,
            ansible_version,
        ))

    def parse_args(self, *args, **kwargs):
        args = Script.parse_args(self, *args, **kwargs)

        if args.version:
            self.show_version()
            self.exit(0)

        if args.inventory is None:
            self.exit(1, 'Could not detect default inventory path')

        if 'pattern' in args and not Inventory(args.inventory).list_hosts(args.pattern):
            self.exit(1, 'No hosts matched')

        if args.ask_pass:
            args.remote_pass = getpass.getpass('Enter remote user password: ')
        else:
            args.remote_pass = None

        if args.ask_become_pass:
            args.become_pass = getpass.getpass('Enter become password: ')
        else:
            args.become_pass = None

        if args.become:
            self.mode = 'become %s ' % args.become_user
        elif args.su:
            self.mode = 'su %s ' % args.su_user

        if args.vault_password_file:
            args.vault_pass = utils.read_vault_file(args.vault_password_file)
        else:
            args.vault_pass = False

        return args


class AnsibleScript(GenericAnsibleScript):
    """Ansible script wrapper

    Extend systematic.shell.Script (which wraps argparse.ArgumentParser) to run ansible
    commands with reports.
    """
    runner_class = AnsibleRunner

    def __init__(self, *args, **kwargs):
        GenericAnsibleScript.__init__(self, *args, **kwargs)
        self.add_common_arguments()
        self.add_default_arguments()

    def add_common_arguments(self):
        self.add_argument('-i', '--inventory', default=DEFAULT_HOST_LIST, help='Inventory path')
        self.add_argument('-M', '--module-path', default=DEFAULT_MODULE_PATH, help='Ansible module path')
        self.add_argument('-T', '--timeout', type=int, default=DEFAULT_TIMEOUT, help='Response timeout')
        self.add_argument('-u', '--user', default=active_user, help='Remote user')
        self.add_argument('-U', '--become-user', default=DEFAULT_become_USER, help='become user')
        self.add_argument('--private-key', default=DEFAULT_PRIVATE_KEY_FILE, help='Private key file')
        self.add_argument('-f', '--forks', type=int, default=DEFAULT_FORKS, help='Ansible concurrency')
        self.add_argument('--port', type=int, default=DEFAULT_REMOTE_PORT, help='Remote port')
        self.add_argument('-S','--su', action='store_true', help='run operations with su')
        self.add_argument('-s','--become', action='store_true', help='run operations with become (nopasswd)')
        self.add_argument('-k', '--ask-pass', action='store_true', help='Ask for SSH password')
        self.add_argument('-K', '--ask-become-pass', action='store_true', help='Ask for become password')
        self.add_argument('-c', '--colors', action='store_true', help='Show output with colors')

    def add_default_arguments(self):
        self.add_argument('-m', '--module', default=DEFAULT_MODULE_NAME, help='Ansible module name')
        self.add_argument('-a', '--args', default=DEFAULT_MODULE_ARGS, help='Module arguments')
        self.add_argument('pattern', nargs='*', default=DEFAULT_PATTERN, help='Ansible host pattern')

    def parse_args(self):
        return GenericAnsibleScript.parse_args(self)

    def run(self, args):
        runner = self.runner_class(
            host_list=os.path.realpath(args.inventory),
            module_path=args.module_path,
            module_name=args.module,
            module_args=args.args,
            forks='%d' % args.forks,
            timeout=args.timeout,
            pattern=args.pattern,
            remote_user=args.user,
            remote_pass=args.remote_pass,
            remote_port=args.port,
            private_key_file=args.private_key,
            su=args.su,
            become=args.become,
            become_user=args.become_user,
            become_pass=args.become_pass,
            show_colors=args.colors,
        )

        try:
            return runner.run()
        except AnsibleError, emsg:
            raise RunnerError(emsg)


class PlaybookScript(GenericAnsibleScript):
    """Playbook runner wrapper

    Extend systematic.shell.Script (which wraps argparse.ArgumentParser) to run ansible
    playbooks with reports.
    """
    runner_class = PlaybookRunner

    def __init__(self, *args, **kwargs):
        GenericAnsibleScript.__init__(self, *args, **kwargs)
        self.add_common_arguments()
        self.add_argument('playbook', nargs='?', help='Ansible playbook path')

    def add_common_arguments(self):
        self.add_argument('-i', '--inventory', default=DEFAULT_HOST_LIST, help='Inventory path')
        self.add_argument('-M', '--module-path', default=DEFAULT_MODULE_PATH, help='Ansible module path')
        self.add_argument('-T', '--timeout', type=int, default=DEFAULT_TIMEOUT, help='Response timeout')
        self.add_argument('-u', '--user', default=active_user, help='Remote user')
        self.add_argument('-U', '--become-user', default=DEFAULT_BECOME_USER, help='become user')
        self.add_argument('--private-key', default=DEFAULT_PRIVATE_KEY_FILE, help='Private key file')
        self.add_argument('-f', '--forks', type=int, default=DEFAULT_FORKS, help='Ansible concurrency')
        self.add_argument('--port', type=int, default=DEFAULT_REMOTE_PORT, help='Remote port')
        self.add_argument('-S','--su', action='store_true', help='run operations with su')
        self.add_argument('-s','--become', action='store_true', help='run operations with become (nopasswd)')
        self.add_argument('-k', '--ask-pass', action='store_true', help='Ask for SSH password')
        self.add_argument('-K', '--ask-become-pass', action='store_true', help='Ask for become password')
        self.add_argument('-a', '--args', default=DEFAULT_MODULE_ARGS, help='Module arguments')
        self.add_argument('-c', '--colors', action='store_true', help='Show output with colors')
        self.add_argument('--show-facts', action='store_true', help='Show ansible facts in results')
        self.add_argument('--vault-password-file', default=None, help='Vault password path')

    def parse_args(self):
        """Parse arguments and run playbook

        Parse provided arguments and run the playbook with ansiblereporter.result.PlaybookRunner.run()
        """
        return GenericAnsibleScript.parse_args(self)

    def run(self, args):
        if not args.playbook:
            self.exit(1, 'No playbook provided')

        runner = self.runner_class(
            playbook=args.playbook,
            host_list=os.path.realpath(args.inventory),
            module_path=args.module_path,
            forks='%d' % args.forks,
            timeout=args.timeout,
            remote_user=args.user,
            remote_pass=args.remote_pass,
            become_pass=args.become_pass,
            remote_port=args.port,
            transport='smart',
            private_key_file=args.private_key,
            become=args.become,
            become_user=args.become_user,
            extra_vars=None,
            only_tags=None,
            skip_tags=None,
            subset=None,
            inventory=None,
            check=False,
            diff=False,
            any_errors_fatal=False,
            vault_password=args.vault_pass,
            force_handlers=False,
            show_colors=args.colors,
            show_facts=args.show_facts,
        )

        try:
            return runner.run()
        except AnsibleError, emsg:
            raise RunnerError(emsg)
