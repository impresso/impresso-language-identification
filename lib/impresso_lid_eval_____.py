#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Evaluate the results of impresso lid against goldstandard
"""
__version__ = "2020.12.14"
__appname__ = "[application name here]"
__author__  = "simon.clematide@uzh.ch"


import logging
import sys
import os
import re
from collections import defaultdict
import jsonlines
from smart_open import open

log = logging.getLogger(__name__)

def read_tsv_goldstandard(filename):
    result = []
    with open(filename,'r') as f:
        for l in f:
            if l.strip() == "":
                continue
            else:
                impresso_id,lang = l.split("\t")
                result.append((impresso_id,lang))
    result.sort()
    return result

    def read_data(self):
        """


        {"tp":"ar","cid":"diekwochen-1848-02-12-a-i0004","len":3899,"orig_lg":"lb","langdetect":[{"lang":"fr","prob":1}],"langid":[{"lang":"fr","prob":1}]}

        """

        json_reader = jsonlines.Reader(sys.stdin)
        for jdata in json_reader:
            m = re.search(r'^(?P<COLLECTION>.+)-(?P<YEAR>\d{4})-(?P<MONTH>\d{2})-(?P<DAY>\d{2})-(?P<EDITION>[a-z])-i(?P<CONTENTITEM>\d{4})$', jdata["id"])
            if m is not None:
                self.data[(m["COLLECTION"],m["YEAR"])].append(jdata["id"])
                self.data_info[jdata["id"]] = jdata
            else:
                log.error(f'NO MATCH FOR CONTENTITEM {jdata["id"]}')

            for k in self.data:
                self.data[k].sort()


class MainApplication(object):

    def __init__(self, args):
        self.args = args
        self.data = defaultdict(list)
        self.data_info = {}

    def search_json_lines(self):
        for (collection, year) in sorted(self.data):
            articles = {k for k in self.data[(collection, year)]}
            log.debug(f'Found {len(articles)} articles in collection {collection}-{year}')
            filename = f'{self.args.data_dir}/{collection}/{collection}-{year}.{self.args.file_extension}'
            log.debug(f'Working on {filename}')
            if not os.path.exists(filename):
                log.warning(f"File {filename} does not exist. Ignoring it for now...")
                continue
            with open(filename,encoding="utf-8") as reader:
                json_reader = jsonlines.Reader(reader)
                for jdata in json_reader:
#                   print(jdata)
                    if jdata["id"] in articles:
                        lng_id_info = self.data_info[jdata["id"]]
                        print(
                            lng_id_info["orig_lg"],
                            len(jdata["ft"]),
                            lng_id_info[self.args.language_identifier],
                            #lng_id_info[self.args.language_identifier][0]["lang"],
                            sep="\t"
                        )
                        del articles[jdata["id"]]
                        if articles == {}:
                            break
#            print(self.data[(collection, year)])

    def read_data(self):
        """


        {"tp":"ar","cid":"diekwochen-1848-02-12-a-i0004","len":3899,"orig_lg":"lb","langdetect":[{"lang":"fr","prob":1}],"langid":[{"lang":"fr","prob":1}]}

        """
        json_reader = jsonlines.Reader(sys.stdin)
        for jdata in json_reader:
            m = re.search(r'^(?P<COLLECTION>.+)-(?P<YEAR>\d{4})-(?P<MONTH>\d{2})-(?P<DAY>\d{2})-(?P<EDITION>[a-z])-i(?P<CONTENTITEM>\d{4})$', jdata["id"])
            if m:
                self.data[(m["COLLECTION"],m["YEAR"])].append(jdata["id"])
                self.data_info[jdata["id"]] = jdata
            else:
                log.error(f'NO MATCH FOR CONTENTITEM {jdata["id"]}')

            for k in self.data:
                self.data[k].sort()

    def run(self):
        log.debug(self.args)
        self.read_data()
        log.debug(self.data)
        self.search_json_lines()

if __name__ == '__main__':
    import argparse
    description = ""
    epilog = ""
    parser = argparse.ArgumentParser(description=description, epilog=epilog)
    parser.add_argument('-l', '--logfile', dest='logfile',
                      help='write log to FILE', metavar='FILE')
    parser.add_argument('-v', '--verbose', dest='verbose',default=2,type=int, metavar="LEVEL",
                      help='set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)')
    parser.add_argument('--data-dir', default='.',
                        help='prefix of data dir for jsonl files (default %(default)s)')
    parser.add_argument('--file-extension', default='jsonl.bz2',
                      help='suffix for data files (without initial period) (default %(default)s)')

    #parser.add_argument(
    #    "infile",
    #    metavar="INPUT",
    #    type=str,
    #    help="Input files of the format *.tsv ",
    #)


    args = parser.parse_args()

    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
                  logging.INFO, logging.DEBUG]
    logging.basicConfig(level=log_levels[args.verbose],
                        format='%(asctime)-15s %(levelname)s: %(message)s')

    # launching application ...
    MainApplication(args).run()
