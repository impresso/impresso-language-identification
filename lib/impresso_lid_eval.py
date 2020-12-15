#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Evaluate the results of impresso lid against goldstandard
"""
__version__ = "2020.12.14"
__appname__ = "[application name here]"
__author__ = "simon.clematide@uzh.ch"

import logging
import os
import re
import sys
import json
from collections import defaultdict, Counter
from typing import DefaultDict, Dict, Tuple

import jsonlines
from smart_open import open

log = logging.getLogger(__name__)


class ImpressoLIDEvaluation(object):
    def __init__(self, config={}):
        self.config: dict = config
        """External configuration from commandline"""

        self.ids_per_coll_year: DefaultDict[Tuple[str, str], list] = defaultdict(list)
        """Mapping of tuple (collection, year) to the list of its (sorted) content item ids"""

        self.id2data: Dict[str, dict] = {}
        """Mapping of content item id to JSON content of sampling file"""

        self.stats: DefaultDict[Counter] = defaultdict(Counter)
        """Evaluation statistics: _ALL_ for statistics over all languages, and ll iso-latin codes for all other languages, ll__ll for confusion"""

    def search_json_lines(self):
        """Update information for each sampled content item in self.id2data"""

        for (collection, year) in sorted(self.ids_per_coll_year):
            articles = {k for k in self.ids_per_coll_year[(collection, year)]}
            log.debug(
                f"Found {len(articles)} articles in collection {collection}-{year}"
            )
            filename = f"""{self.config["data_dir"]}/{collection}/{collection}-{year}.{self.config["file_extension"]}"""
            log.info(f"Working on {filename}")
            if not os.path.exists(filename):
                log.warning(f"File {filename} does not exist. Ignoring it for now...")
                continue
            with open(filename, encoding="utf-8") as reader:
                json_reader = jsonlines.Reader(reader)
                for jdata in json_reader:
                    if jdata["id"] in articles:
                        log.info(f"ADDED article {jdata['id']}")
                        self.id2data[jdata["id"]].update(jdata)
                        articles.discard(jdata["id"])
                        if articles == {}:
                            break

    def eval_json(self):
        """"""

        for cid, ci in self.id2data.items():
            if (gold_lg := ci.get("gold_lg")) is not None:
                if (lg := ci.get("lg")) is not None:
                    self.stats["_ALL_"][lg == gold_lg] += 1
                    self.stats[gold_lg][lg == gold_lg] += 1
                    if lg != gold_lg:
                        self.stats["__".join(gold_lg, lg)][False] += 1
        log.debug(f"STATS {self.stats}")
    #            print(self.ids_per_coll_year[(collection, year)])

    def read_sampling_data(self):
        """
        {"tp":"ar","cid":"diekwochen-1848-02-12-a-i0004","len":3899,"orig_lg":"lb","langdetect":[{"lang":"fr","prob":1}],"langid":[{"lang":"fr","prob":1}]}
        """
        json_reader = jsonlines.Reader(sys.stdin)
        for jdata in json_reader:
            m = re.search(
                r"^(?P<COLLECTION>.+)-(?P<YEAR>\d{4})-(?P<MONTH>\d{2})-(?P<DAY>\d{2})-(?P<EDITION>[a-z])-i(?P<CONTENTITEM>\d{4})$",
                jdata["id"],
            )
            if m:
                self.ids_per_coll_year[(m["COLLECTION"], m["YEAR"])].append(jdata["id"])
                self.id2data[jdata["id"]] = jdata
            else:
                log.error(f'NO MATCH FOR CONTENTITEM {jdata["id"]}')

            for k in self.ids_per_coll_year:
                self.ids_per_coll_year[k].sort()

    def print_statistics(self):
        if self.config["output_format"] == "json":
            result = {"acc": {}, "corr": {}, "wrong": {}, "confusion": {}}
            for k, stat in self.stats.items():
                k_total = sum(c for v,c in stat.items() if isinstance(v,bool))
                result["corr"][k] = stat[True]
                result["wrong"][k] = stat[False]
                result["acc"][k] = stat[True] / k_total
                result["confusion"][k] = stat[k]
            print(json.dumps(result))
        if self.config["diagnostics_json"]:
            with open(self.config["diagnostics_json"], "w",encoding="utf-8") as f:
                for cid in self.id2data:
                    print(json.dumps(self.id2data[cid]), file=f)

    def run(self):
        log.debug(self.config)
        self.read_sampling_data()
        self.search_json_lines()
        self.eval_json()
        self.print_statistics()


if __name__ == "__main__":
    import argparse

    description = ""
    epilog = ""
    parser = argparse.ArgumentParser(description=description, epilog=epilog)
    parser.add_argument(
        "-l", "--logfile", dest="logfile", help="write log to FILE", metavar="FILE"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        default=2,
        type=int,
        metavar="LEVEL",
        help="set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)",
    )
    parser.add_argument(
        "--ids_per_coll_year-dir",
        default=".",
        help="prefix of ids_per_coll_year dir for jsonl files (default %(default)s)",
    )
    parser.add_argument(
        "--file-extension",
        default="jsonl.bz2",
        help="suffix for ids_per_coll_year files (without initial period) (default %(default)s)",
    )
    parser.add_argument(
        "--diagnostics-json",
        type=str,
        help="diagnostics information",
    )
    parser.add_argument(
        "--data-dir",
        default=".",
        help="prefix of data dir for jsonl files (default %(default)s)",
    )
    parser.add_argument(
        "--output-format",
        default="json",
        help="output format (default %(default)s)",
    )

    args = parser.parse_args()

    log_levels = [
        logging.CRITICAL,
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
    ]
    logging.basicConfig(
        level=log_levels[args.verbose],
        format="%(asctime)-15s %(levelname)s: %(message)s",
    )

    ImpressoLIDEvaluation(config=vars(args)).run()
