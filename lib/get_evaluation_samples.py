#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""


bzcat data/processed-canonical-data/language_identification/v01-stage1/*/*.bz2 | jq -r  'select(.orig_lg == "lb" and .tp == "ar" and .len > 300 and .langid[0].lang != "lb")|[.cid,.tp,.orig_lg,.len,.langdetect[0].lang,.langdetect[0].prob,.langid[0].lang,.langid[0].prob]|@tsv' > sample1.tsv
bzcat data/processed-canonical-data/language_identification/v01-stage1/*/*.bz2 | jq -c 'select(.orig_lg == "lb" and .tp == "ar" and .len > 300 and .langid[0].lang != "lb")|[.cid,.tp,.orig_lg,.len,.langdetect[0].lang,.langdetect[0].prob,.langid[0].lang,.langid[0].prob]'

bzcat data/processed-canonical-data/language_identification/v01-stage1/*/*.bz2 | jq -c 'select(.orig_lg == "lb" and .tp == "ar" and .len > 300 and .langid[0].lang != "lb")'

bzcat data/processed-canonical-data/language_identification/v01-stage1/*/*.bz2 | jq -r  'select(.orig_lg == "lb" and .tp == "ar" and .len > 300 and .langid[0].lang == "lb")|[.cid,.tp,.orig_lg,.len,.langdetect[0].lang,.langdetect[0].prob,.langid[0].lang,.langid[0].prob]|@tsv' > sample2.tsv

$ python lib/get_evaluation_samples.py < sample1.json -C data/canonical-rebuilt > sample0.results.tsv -v 4

bzcat data/processed-canonical-data/language_identification/v01-stage1/*/*.bz2 | jq -c  'select(.tp == "ar" and .len > 300 and .langid[0].lang == "lb" and .langid[0].prob > 0.95)'

"""

__appname__ = "[application name here]"
__author__  = "AA"
__version__ = "0.0pre0"
__license__ = "GNU GPL 3.0 or later"

import logging
log = logging.getLogger(__name__)
import sys
import re
from collections import defaultdict
import jsonlines
from smart_open import open

def snippify_text(text, max_len=1201):
    text = "TXT: " + text.replace("\t"," ")


    if len(text) <= max_len:
        return text
    else:
        return text[0:max_len//2]+" [...] " + text[-max_len//2:]



class MainApplication(object):

    def __init__(self, args):
        self.args = args
        self.data = defaultdict(list)
        self.data_info = {}

    def search_json_lines(self):
        for (collection, year) in sorted(self.data):
            articles = {k:True for k in self.data[(collection, year)]}
            log.debug(f'Found {len(articles)} articles in collection {collection}-{year}')
#            print(articles)
            filename = f'{self.args.data_dir}/{collection}/{collection}-{year}.jsonl.bz2'
            log.debug(f'Working on {filename}')
            with open(filename,encoding="utf-8") as reader:
                json_reader = jsonlines.Reader(reader)
                for jdata in json_reader:
#                   print(jdata)
                    if jdata["id"] in articles:
                        lng_id_info = self.data_info[jdata["id"]]
                        print(f'{self.args.http_prefix}/{jdata["id"]}',
                            lng_id_info["orig_lg"],
                            len(jdata["ft"]),
                            snippify_text(jdata["ft"]),
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
            m = re.search(r'^(?P<COLLECTION>.+)-(?P<YEAR>\d{4})-(?P<MONTH>\d{2})-(?P<DAY>\d{2})-(?P<EDITION>[a-z])-i(?P<CONTENTITEM>\d{4})$', jdata["cid"])
            if m:
                self.data[(m["COLLECTION"],m["YEAR"])].append(jdata["cid"])
                self.data_info[jdata["cid"]] = jdata
            else:
                log.error(f'NO MATCH FOR CONTENTITEM {d}')

            for k in self.data:
                self.data[k].sort()

    def run(self):
        log.debug(self.args)
        self.read_data()
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
    parser.add_argument('-C', '--data-dir', default='.',
                      help='prefix of data dir for jsonl files (default %(default)s)')
    parser.add_argument('-H', '--http-prefix', default='https://impresso-project.ch/app/article',
                      help='prefix of data dir for jsonl files (default %(default)s)')
    parser.add_argument('-m', '--mode', default=0,
                      help='mode of operation (default %(default)s)')
    parser.add_argument('-L', '--language-identifier', default="langid",choices=["langid","langdetect","lg"],
                      help='select language identifier tool (default %(default)s)')

    args = parser.parse_args()

    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
                  logging.INFO, logging.DEBUG]
    logging.basicConfig(level=log_levels[args.verbose],
                        format='%(asctime)-15s %(levelname)s: %(message)s')

    # launching application ...
    MainApplication(args).run()
