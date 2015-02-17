from __future__ import print_function
import six  # noqa
from collections import defaultdict, Iterable, deque
from .. import sources
from metadatastore.api import Document
import os
# Note: Invoke contents of sources at the func/method level so that it
# respects runtime switching between real and dummy sources.


# These should be specified elsewhere in a way that can be easily updated.
# This is merely a placeholder, but it can be used with the real
# channelarchiver as well as the dummy one.


class DataBroker(object):

    @classmethod
    def __getitem__(cls, key):
        # Define imports here so that sources can be switched
        # at run time.
        find_last = sources.metadatastore.api.find_last
        find_run_start = sources.metadatastore.api.find_run_start
        return_list = True
        if isinstance(key, slice):
            # Slice on recent runs.
            if key.start is not None and key.start > -1:
                raise ValueError("Slices must be negative. The most recent "
                    "run is referred to as -1.")
            if key.stop is not None and key.stop > -1:
                raise ValueError("Slices must be negative. The most recent "
                    "run is referred to as -1.")
            if key.stop is not None:
                num = key.stop - key.start
            else:
                num = 1
            headers = find_last(-key.start)[:num:key.step]
        elif isinstance(key, int):
            return_list = False
            if key > -1:
                headers = find_run_start(scan_id=key)
                if len(headers) == 0:
                    raise ValueError("No such run found.")
            else:
                headers = find_last(-key)
        else:
            ValueError("Must give an integer scan ID like [6] or a slice "
                       "into past scans like [-5], [-5:], or [-5:-9:2].")
        [_build_header(h) for h in headers]
        if not return_list:
            headers = headers[0]
        return headers

    @classmethod
    def fetch_events(cls, runs, ca_host=None, channels=None):
        """
        Get Events from given run(s).

        Parameters
        ----------
        runs : one RunHeader or a list of them
        ca_host : URL string
            the URL of your archiver's ArchiveDataServer.cgi. For example,
            'http://cr01arc01/cgi-bin/ArchiveDataServer.cgi'
        channels : list, optional
            All queries will return applicable data from the N most popular
            channels. If data from additional channels is needed, their full
            identifiers (not human-readable names) must be given here as a list
            of strings.

        Returns
        -------
        data : a flat list of Event objects
        """
        find_event = sources.metadatastore.api.find_event

        if not isinstance(runs, Iterable):
            runs = [runs]

        runs = [find_event(run) for run in runs]
        descriptors = [descriptor for run in runs for descriptor in run]
        events = [event for descriptor in descriptors for event in descriptor]
        [fill_event(event) for event in events]

        if channels is not None:
            if ca_host is None:
                ca_host = _get_local_ca_host()
            all_times = [event.time for event in events]
            archiver_data = _get_archiver_data(ca_host, channels,
                                               min(all_times), max(all_times))
        return events

    @classmethod
    def find_headers(cls, **kwargs):
        """
        For now, pass through to metadatastore.api.analysis.find_header

        Parameters
        ----------
        **kwargs

        Returns
        -------
        data : list
            Header objects
        """
        find_header = sources.metadatastore.api.find_header
        run_start = find_header(**kwargs)
        headers = [_build_header(rs) for rs in run_start]
        return headers


def _get_archiver_data(ca_host, channels, start_time, end_time):
    archiver = sources.channelarchiver.Archiver(ca_host)
    archiver_result = archiver.get(channels, start_time, end_time,
                                   interpolation='raw')  # never interpolate
    # Put archiver data into Documents, minimicking a MDS event stream.
    events = list()
    for ch_name, ch_data in zip(channels, archiver_result):
        # Build a Event Descriptor.
        descriptor = dict()
        descriptor.time = start_time
        descriptor['data_keys'] = {ch_name: dict(source=ch_name,
                                   shape=[], dtype='number')}
        descriptor = Document(descriptor)
        for time, value in zip(ch_data.times, ch_data, values):
            # Build an Event.
            event = dict()
            event['descriptor'] = descriptor
            event['time'] = time
            event['data'] = {ch_name: (value, time)}
            event = Document(event)
            events.append(event)
    return events


class LocationError(ValueError):
    pass


def _get_local_ca_host():
    """Obtain the url for the cahost by using the uname() function to
    grab the local beamline id

    References
    ----------
    https://github.com/NSLS-II/channelarchiver/README.rst
    """
    beamline_id = os.uname()[1][:4]
    if not beamline_id.startswith('xf'):
        raise LocationError('You are not on a registered beamline computer. '
                            'Unable to guess which channel archiver to use. '
                            'Please specify the channel archiver you wish to'
                            'obtain data from.')
    return 'http://' + beamline_id + '-ca/cgi-bin/ArchiveDataServer.cgi'


def _inspect_descriptor(descriptor):
    """
    Return a dict with the data keys mapped to boolean answering whether
    data is external.
    """
    # TODO memoize to cache these results
    data_keys = descriptor.data_keys
    is_external = defaultdict(lambda: False)
    for data_key, data_key_dict in data_keys.items():
        if 'external' in data_key_dict:
            is_external[data_key] = True
    return is_external


def fill_event(event):
    """
    Populate events with externally stored data.
    """
    retrieve_data = sources.filestore.commands.retrieve_data
    is_external = _inspect_descriptor(event.descriptor)
    for data_key, (value, timestamp) in event.data.items():
        if is_external[data_key]:
            # Retrieve a numpy array from filestore
            event.data[data_key][0]= retrieve_data(value)


def _build_header(run_start):
    fed = sources.metadatastore.api.find_event_descriptor
    run_start.event_descriptors = fed(run_start)
    # TODO merge contents of RunEnd
    run_start._name = 'Header'