"""
Ansible runner wrapper for output reporting tasks
"""

import os
import json

from datetime import datetime
from ansible.playbook import PlayBook
from ansible.runner import Runner
from seine.address import IPv4Address
from systematic.log import Logger

from ansiblereporter import SortedDict, RunnerError
from ansiblereporter.reporter_callbacks import AggregateStats, PlaybookCallbacks, PlaybookRunnerCallbacks


RESULT_DATE_FORMAT = '%Y-%m-%d %H:%M:%S.%f'


class Result(SortedDict):
    """Ansible result

    Single result from ansible command or playbook

    """
    compare_fields = ( 'resultset', 'address', 'host', )
    def __init__(self, resultset, host, data):
        SortedDict.__init__(self)
        self.resultset = resultset
        self.host = host

        self.__cached_properties__ = {}

        try:
            self.address = IPv4Address(host)
        except ValueError:
            self.address = None

        self.update(**data)

    def __repr__(self):
        if 'end' in self:
            return ' '.join([self.host, self.status, self.end.strftime('%Y-%m-%d %H:%M:%S')])
        else:
            return ' '.join([self.host, self.status])

    def __set_cached_property__(self, key, value):
        """Set cached property value

        Store given value for key to self.__cached_properties__. Returns nothing.
        """
        self.__cached_properties__[key] = value

    def __get_cached_property__(self, key):
        """Get cached properties

        Return cached property value from self.__cached_properties__ or None
        if key was not stored.
        """
        return self.__cached_properties__.get(key, None)

    def __get_datetime_property__(self, key):
        """Parse cached datetime property

        Attempts to retrieve given property and parse it as datetime using
        RESULT_DATE_FORMAT format.

        Returns None if value was not found or invalid, and datetime object if
        value was found and could be parsed.

        Successfully parsed value is stored as cached property and not
        calculated again.
        """
        cached = self.__get_cached_property__(key)
        if cached:
            return cached

        value = self.get(key, None)
        if value is not None:
            try:
                value = datetime.strptime(value, RESULT_DATE_FORMAT)
            except ValueError:
                raise RunnerError('Error parsing %w date value %s' (key, value))
        else:
            return None

        self.__set_cached_property__(key, value)
        return value

    def __parse_custom_status_codes__(self):
        """Parse custom status

        Override this function to parse status code from custom modules.

        By default this always returns 'unknown'
        """
        return 'unknown'

    @property
    def show_colors(self):
        """Should be show colors

        Accessor to runner's show_colors flag. Used for result processing
        in callbacks
        """
        return self.resultset.runner.show_colors

    @property
    def changed(self):
        """Return changed boolean flag

        If changed flag is found, return boolean test result for it,
        otherwise return False.

        This property always returns boolean, checking if value evaluates
        to True.
        """
        value = self.get('changed', False)
        return value and True or False

    @property
    def returncode(self):
        """Return code

        Return key 'rc' as integer in range 0-255. If value is not
        integer or not in range, return 255.

        If 'rc' key is not found, return 0
        """
        try:
            return int(self.get('rc', 0))
            if rc < 0 or rc > 255:
                raise ValueError
        except ValueError:
            return 255

    @property
    def error(self):
        """Return error message or 'UNKNOWN ERROR'

        If error message is set in 'msg' key, return it, otherwise
        return 'UNKNOWN ERROR' string.

        This property does not check if result status actually was
        error, must returns the 'msg' key if found.
        """
        return self.get('msg', 'UNKNOWN ERROR')

    @property
    def stdout(self):
        """Return stdout or ''

        Return stdout or empty string
        """
        return self.get('stdout', '')

    @property
    def stderr(self):
        """Return stderr or ''

        Return stderr or empty string
        """
        return self.get('stderr', '')

    @property
    def state(self):
        """Return result state

        Returns host state i.e. resultset name ('contacted', 'dark')
        """
        return self.resultset.name

    @property
    def ansible_facts(self):
        """Return facts for host

        If ansible facts were collected, return the dictionary.

        Otherwise return None.
        """
        return self.resultset.ansible_facts.get(self.host, None)

    @property
    def start(self):
        """Return start as datetime

        Return start end date as datetime or None if not found
        """
        return self.__get_datetime_property__('start')

    @property
    def end(self):
        """Return end as datetime

        Return task end date as datetime or None if not found
        """
        return self.__get_datetime_property__('end')

    @property
    def delta(self):
        """Return end - start timedelta value

        Calculate the 'delta' field again, returning proper datetime.timedelta
        value from self['end'] - self['start'] instead of string in dictionary.

        Returns None if either start or end is not a datetime object.
        """
        cached = self.__get_cached_property__('delta')
        if cached:
            return cached

        end = self.end
        start = self.start

        if not isinstance(end, datetime) or not isinstance(start, datetime):
            return None

        value = end - start
        self.__set_cached_property__('delta', value)
        return value

    @property
    def module_name(self):
        """Module name

        Return invocated module name or empty string if not available.

        Successfully parsed value is stored as cached property and not
        calculated again.
        """
        cached = self.__get_cached_property__('module_name')
        if cached:
            return cached

        try:
            value = self['invocation']['module_name']
        except KeyError:
            return ''

        self.__set_cached_property__('module_name', value)
        return value

    @property
    def module_args(self):
        """Module args

        Similar to command property, returning module_args, but always
        returns the module_args value or empty string, never returning
        module_name like self.command

        Successfully parsed value is stored as cached property and not
        calculated again.
        """
        cached = self.__get_cached_property__('module_args')
        if cached:
            return cached

        try:
            value = self['invocation']['module_args']
        except KeyError:
            return ''

        self.__set_cached_property__('module_args', value)
        return value

    @property
    def command(self):
        """Return executed command

        For shell and command moudles, return module_args from
        self['invocation']['module_args'] or empty string if not available.

        For any other module return module name.

        Successfully parsed value is stored as cached property and not
        calculated again.
        """
        cached = self.__get_cached_property__('command')
        if cached:
            return cached

        if self.module_name in ( 'command', 'shell' ):
            try:
                value = self['invocation']['module_args']
            except KeyError:
                return ''
        else:
            value = self.module_name

        self.__set_cached_property__('command', value)
        return value

    @property
    def status(self):
        """Return result status

        Parse result status, depending on the module being run.
        Return values are:
          'ok'      shell/command returned code 0, ping returned 'pong'
          'error'   shell/command returned other code than 0
          'failed'  module run failed (result contains 'failed' key), ping
                    did not return
          'facts'   ansible facts were parsed successfully
          'pending_facts'
                    ansible facts were expected but not received
          'unknown' status could not be parsed

        To extend status parsing for custom modules, please implement the
        __parse_custom_status_codes__ function in child class.

        Successfully parsed status is stored as cached property and not
        calculated again.
        """
        cached = self.__get_cached_property__('status')
        if cached:
            return cached

        if 'failed' in self:
            value = 'failed'

        elif 'rc' in self:
            if self.get('rc', 0) == 0:
                value = 'ok'
            else:
                value = 'error'

        elif self.module_name == 'ping':
            value = self.get('ping', None) == 'pong' and 'ok' or 'failed'

        elif self.module_name == 'setup':
            if self.get('ansible_facts', None):
                value = 'facts'
            else:
                return 'pending_facts'

        else:
            return self.__parse_custom_status_codes__()

        self.__set_cached_property__('status', value)
        return value

    @property
    def ansible_status(self):
        """Return ansible style status string

        Return status string as shown by ansible command output:
        success: command was successful
        FAILED: command failed
        """
        if self.status == 'ok':
            return 'success'

        elif self.status in ( 'failed', 'error', ):
            return 'FAILED'

        return self.status

    def copy(self):
        return Result(self.resultset, self.host, self)

    def write_to_directory(self, directory, formatter, extension):
        """Write file to directory with formatter callback

        Write result to given directory with path like:

          directory/<self.host>.<extension>

        Callback is used for formatting of the text in the file.

        Raises RunnerError if file writing failed.
        """
        filename = os.path.join(directory, '%s.%s' % (self.host, extension))
        self.log.debug('writing to %s' % filename)

        try:
            open(filename, 'w').write('%s\n' % formatter(self))

        except IOError, (ecode, emsg):
            raise RunnerError('Error writing file %s: %s' % (filename, emsg))
        except OSError, (ecode, emsg):
            raise RunnerError('Error writing file %s: %s' % (filename, emsg))

    def format(self, callback):
        """Format data

        Format this result with callback function. Used for writing files
        """
        return callback(self)

    def to_json(self, indent=2):
        """Return as json

        Return dictionary as json data. No custom properties are added by default.
        """
        return json.dumps(self, indent=indent)


class ResultSet(list):
    """Set of ansible results

    ResultSet is group of ansible results (contacted, dark). ResultSets
    are created by ResultList parsing.

    Each result is loaded with class attribute result_loader which defaults
    to Result.
    """

    def __init__(self, resultset, name):
        self.log = Logger().default_stream
        self.resultset = resultset
        self.name = name
        self.ansible_facts = {}

    @property
    def result_loader(self):
        return self.resultset.runner.result_loader

    def append(self, host, result):
        """Append a result

        Results are appended with self.result_loader, which must be subclass of
        Result class.

        If the result contains ansible facts (key ansible_facts), parent result list's
        cached copy of ansible facts is overwritten.
        """
        list.append(self, self.result_loader(self, host, result))

        if 'ansible_facts' in result:
            self.ansible_facts[host] = result['ansible_facts']

    def to_json(self, indent=2):
        """"Return as json

        Returns list of results formatted as json

        """
        return json.dumps(self, indent=indent)


class ResultList(object):
    """List of results

    Parent class for collected results, with two result sets in self.results:

    self.results['contacted'] = result set of contacted hosts
    self.results['dark'] = result set of unreachable hosts

    Note: a host may be in both sets if it was unreachable in middle of a playbook
    """

    def __init__(self, runner, show_colors=False):
        self.log = Logger().default_stream
        self.runner = runner
        self.show_colors = show_colors

        self.results = {
            'contacted': self.resultset_loader(self, 'contacted'),
            'dark': self.resultset_loader(self, 'dark'),
        }


    @property
    def resultset_loader(self):
        return self.runner.resultset_loader

    def sort(self):
        """Sort results

        Sorts the ResultSets contacted and dark

        """
        self.results['dark'].sort()
        self.results['contacted'].sort()

    def to_json(self, indent=2):
        """Return as json

        Returns all results formatted to json

        """
        return json.dumps({
                'contacted': self.results['contacted'],
                'dark': self.results['dark'],
            },
            indent=indent
        )

    def write_to_file(self, filename, formatter=None, json=False):
        """Write results to file

        Arguments
          filename: target filename to write
          formatter: callback to format the file entry in text files
          json: if set, formatter is ignored and self.to_json is used to write file

        Either formatter callback or json=True is required

        Raises RunnerError if file writing failed.
        """

        if not formatter and not json:
            raise RunnerError('Either formatter callback or json flag must be set')

        try:
            fd = open(filename, 'w')
            if json:
                fd.write('%s\n' % self.to_json())
            elif formatter:
                for result in self.results['contacted']:
                    fd.write('%s\n' % formatter(result))
                for result in self.results['dark']:
                    fd.write('%s\n' % formatter(result))

            fd.close()

        except IOError, (ecode, emsg):
            raise RunnerError('Error writing file %s: %s' % (filename, emsg))
        except OSError, (ecode, emsg):
            raise RunnerError('Error writing file %s: %s' % (filename, emsg))


class RunnerResults(ResultList):
    """Runner results

    Collect results from ansible runner output

    """

    def __init__(self, runner, results, show_colors=False):
        ResultList.__init__(self, runner, show_colors)

        for k in ( 'dark', 'contacted', ):
            if k in results:
                group = self.results[k]
                for host, result in results[k].items():
                    group.append(host, result)


class PlaybookResults(ResultList, AggregateStats):
    """Playbook results

    Collect results from playbook runs, grouped by host

    """

    def __init__(self, runner, show_colors=False):
        AggregateStats.__init__(self)
        ResultList.__init__(self, runner, show_colors)

    @property
    def grouped_by_host(self):
        """Return task output grouped by host

        Collect task output from different hosts in contacted and dark groups
        and return as dictionary, where each result is grouped under the host.

        """
        data = { 'contacted': [], 'dark': [] }
        res = {}
        for result in self.results['contacted']:
            if result.host not in res:
                res[result.host] = {'host': result.host, 'results': []}

            if result.module_name == 'setup' and not self.runner.show_facts:
                continue

            res[result.host]['results'].append(result.copy())


        for key in sorted(res.keys()):
            data['contacted'].append(res[key])

        res = {}
        for result in self.results['dark']:
            if result.host not in res:
                res[result.host] = {'host': result.host, 'results': []}

            if result.module_name == 'setup' and not self.runner.show_facts:
                continue

            res[result.host]['results'].append(result.copy())

        for key in sorted(res.keys()):
            data['dark'].append(res[key])

        return data

    def compute(self, runner_results, setup=False, poll=False, ignore_errors=False):
        """Import results

        Override for default ansible playbook callback to import task results
        to main process. You can't directly write from tasks without callback to
        main process because they are running in separate processes launched by
        multiprocess module.
        """
        for (host, value) in runner_results.get('contacted', {}).iteritems():
            self.results['contacted'].append(host, value)

        for (host, value) in runner_results.get('dark', {}).iteritems():
            self.results['dark'].append(host, value)

    def summarize(self, host):
        """Return summary

        Return summary for a host.

        TODO - fix this function to actually return what is expected. Right now
        it does not and exists only to override ansible playbook default APIs
        """
        return { 'contacted': self.results['contacted'], 'dark': self.results['dark'], }

    def to_json(self, indent=2):
        """Return as json

        Returns data in json format using self.grouped_by_host for ordering.
        """
        return json.dumps(self.grouped_by_host, indent=indent)

    def write_to_file(self, filename, formatter=None, json=False):
        """Write results to file

        Arguments
          filename: target filename to write
          formatter: callback to format the file entry in text files
          json: if set, formatter is ignored and self.to_json is used to write file

        Either formatter callback or json=True is required

        Raises RunnerError if file writing failed.
        """

        if not formatter and not json:
            raise RunnerError('Either formatter callback or json flag must be set')

        try:
            fd = open(filename, 'w')
            if json:
                fd.write('%s\n' % self.to_json())
            elif formatter:
                for result in self.results['contacted']:
                    if result.module_name == 'setup' and not self.runner.show_facts:
                        continue
                    fd.write('%s\n' % formatter(result))
                for result in self.results['dark']:
                    if result.module_name == 'setup' and not self.runner.show_facts:
                        continue
                    fd.write('%s\n' % formatter(result))
            fd.close()

        except IOError, (ecode, emsg):
            raise RunnerError('Error writing file %s: %s' % (filename, emsg))
        except OSError, (ecode, emsg):
            raise RunnerError('Error writing file %s: %s' % (filename, emsg))


class AnsibleRunner(Runner):
    """Ansible Runner reporter

    Run ansible command and collect results for processing

    """
    resultlist_loader =  RunnerResults
    resultset_loader = ResultSet
    result_loader = Result

    def __init__(self, *args, **kwargs):
        self.show_colors = kwargs.pop('show_colors', False)
        for k in ( 'become_user', 'become_pass', 'become', 'su', 'su_user', 'su_pass', ):
            kwargs.pop(k, None)
        Runner.__init__(self, *args, **kwargs)

    def run(self):
        """Run ansible command and process results

        Run ansible command, returning output processed with
        self.process_results.
        """
        results = Runner.run(self)
        return self.process_results(results, show_colors=self.show_colors)

    def process_results(self, results, show_colors=False):
        """Process collected results

        Called from self.run(), processes collected results.

        Default implementation just sorts the results. Override to
        do more fancy processing.
        """
        return self.resultlist_loader(self, results, show_colors)


class PlaybookRunner(PlayBook):
    """Ansible Playbook reporter

    Run ansible playbook and collect results for processing

    """
    resultlist_loader = PlaybookResults
    resultset_loader = ResultSet
    result_loader = Result

    def __init__(self, *args, **kwargs):
        self.show_colors = kwargs.pop('show_colors', False)
        self.show_facts = kwargs.pop('show_facts', False)

        self.results = self.resultlist_loader(self, self.show_colors)
        self.callbacks = PlaybookCallbacks()
        self.runner_callbacks = PlaybookRunnerCallbacks(self.results)

        kwargs['callbacks'] =self.callbacks
        kwargs['runner_callbacks'] = self.runner_callbacks
        kwargs['stats'] = self.results
        PlayBook.__init__(self, *args, **kwargs)

    def run(self):
        """Run playbook

        Runs playbook and collects output to self.results

        """
        stats = PlayBook.run(self)
        return self.process_results(self.results)

    def process_results(self, results):
        """Process collected results

        Called from self.run(), processes collected results.

        Default implementation just sorts the results. Override to
        do more fancy processing.
        """
        self.results.sort()
        return self.results
