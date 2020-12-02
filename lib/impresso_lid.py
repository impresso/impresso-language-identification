#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""

Decide on language given collection-wide statistics and evidence from language identification tools

Algorithm:
==========
if orig_lg == lb:
    lg = lb
    dec = "lb-oO"
if orig_lg_support > 75%:
    if orig_lg:
        if orig_lg in language:
            lg = orig_lg
            dec ="{lg}-oO"
        else:
            lg = dominant
            dec = "{lg}-oD"
else:
    if article_len > 100:
        if langidlangdetect is None or mean(langidlangdetect_prob) < 0.9  or not in languages:
            lg = dominant
            dec ="{lg}-nD"
        else:
            lg = langidlangdetect
            dec = "{lg}-nL"
    else:
        # article_len =< 100
        lg = dominant
        dec = "{lg}-ntD"

{
  "tp": "ar",
  "id": "luxzeit1858-1859-01-06-a-i0005",
  "len": 361,
  "orig_lg": "fr",
  "version": "2020.11.29",
  "ts": "2020-11-30T08:37:13+00:00",
  "alphabetical_ratio": 0.67,
  "langdetect": [
    {
      "lang": "de",
      "prob": 1
    }
  ],
  "langid": [
    {
      "lang": "de",
      "prob": 1
    }
  ],
  "impresso_ft": [
    {
      "lang": "de",
      "prob": 1
    }
  ],
  "wp_ft": [
    {
      "lang": "de",
      "prob": 0.39
    },
    {
      "lang": "fr",
      "prob": 0.07
    },
    {
      "lang": "cs",
      "prob": 0.06
    }
  ]
}


{
  "collection": "waeschfra",
  "total_orig_support_ratio": 0.828916455220374,
  "textual_content_item_with_orig_lg_count": 14373,
  "textual_content_item_count": 14587,
  "orig_lg_support": {
    "de": 7990.5,
    "fr": 830
  },
  "orig_lg": {
    "de": 11511,
    "fr": 2862,
    "null": 214
  },
  "orig_lg_threshold": {
    "de": 8487,
    "fr": 2154
  },
  "langidlangdetect": {
    "de": 7731,
    "null": 6066,
    "fr": 785,
    "nl": 4,
    "it": 1
  },
  "contentitem_type": {
    "ar": 14373,
    "img": 5366,
    "ad": 210,
    "ob": 1,
    "tb": 3
  },
  "threshold_for_support": 200,
  "dominant_orig_lg": [
    {
      "lang": "de",
      "count": 11511
    },
    {
      "lang": "fr",
      "count": 2862
    }
  ],
  "dominant_langidlangdetect": [
    {
      "lang": "de",
      "count": 7731
    },
    {
      "lang": "fr",
      "count": 785
    }
  ]
}

"""

__version__ = "2020.12.01"

import sys
import copy
import datetime
import json
import logging
from collections import Counter, defaultdict
from typing import Iterable, Optional, Set

import jsonlines
from smart_open import open

log = logging.getLogger(__name__)


def read_json(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


class MainApplication(object):

    def __init__(self, args):

        self.version = __version__
        """Version of the impresso lid script"""

        # self.ts => see self.ts()

        self.args = args
        """Command line arguments"""

        self.attrs_from_content_item = [
            "id",
            "tp",
            "len",
            "orig_lg",
            "alphabetical_ratio"

        ]
        """Defines list of attributes to copy over from stage 1 content items' JSON"""

        self.lids = set(self.args.lids)
        if len(self.lids) < 1:
            print("ERROR: At least one LID system is needed. None provided via option --lids .", file=sys.stderr)
            exit(2)


        self.boosted_lids: Set[str] = set(lid for lid in self.args.boosted_lids if lid == "orig_lg" or lid in self.lids)
        """Set of LIDs that are boosted by a boost factor"""


        self.double_boosted_lids: Set[str] = set(lid for lid in self.args.double_boosted_lids if lid == "orig_lg" or lid in self.boosted_lids)
        """Set of LIDs that are boosted doubly by a boost factor"""


        self.boost_factor: float = self.args.boost_factor
        """Boost factor applied to boosted LIDS if the have support from at least another LID

        The idea is that on their own some of the LIDs or the orig_lg can be pretty wrong. If they have at least a 
        support from a another system the confidence into their decision grows considerably.
        """

        self.admissible_languages: Optional[Set[str]] = \
            set(args.admissible_languages) if args.admissible_languages else None
        """Set of admissible language: If None, no restrictions are applied"""


        self.decision_distribution = Counter()
        self.jsonlog = {}
        self.collection_stats = read_json(self.args.collection_json_stats)
        self.results = []

    def run(self):
        self.decide_language()
        self.output()

    def output(self):
        with open(self.args.output_file, mode="w", encoding="utf-8") as of:
            writer = jsonlines.Writer(of)
            writer.write_all(self.results)

    def next_contentitem(self) -> Iterable[dict]:

        with open(self.args.input_file, encoding="utf-8") as reader:
            json_reader = jsonlines.Reader(reader)
            for jdata in json_reader:
                yield jdata

    def get_best_lid(self, jinfo: dict):
        result = {}
        for lid_system in self.lids:
            lid_preds = jinfo.get(lid_system)
            if lid_preds and len(lid_preds) > 0:
                result[lid_system] = lid_preds[0]
        return result

    def get_votes(self, content_item: dict) -> Optional[Counter]:
        """Return dictionary with boosted votes per language"""

        votes = defaultdict(list)  # for each language key we have a list of tuples (LID, vote_score)

        orig_lg_info = content_item.get('orig_lg')
        # if orig_lg_info:
        #     orig_lg = orig_lg_info[0]["lang"]
        #     support_for_orig_lg = self.collection_stats["orig_lg_support"][orig_lg]
        #     orig_lg_vote_score = (support_for_orig_lg if 'orig_lg' not in self.boosted_lids else
        #                                                            self.boost_factor*support_for_orig_lg)
        #     votes[orig_lg].append(('orig_lg',orig_lg_vote_score))
        for lid in self.lids:

            if lid in content_item and content_item[lid] is not None and len(content_item[lid]) > 0:
                lang, prob = content_item[lid][0]["lang"], content_item[lid][0]["prob"]
                if self.admissible_languages is None or lang in self.admissible_languages:
                    if prob >= self.args.minimal_lid_probability:
                        lang_support = self.collection_stats["lg_support"][lid].get(lang) or 0.0
                        if lang_support:
                            vote_score = 1*prob*lang_support
                            if lid in self.boosted_lids:
                                vote_score *= self.boost_factor
                                if lid in self.double_boosted_lids:
                                    vote_score *= self.boost_factor
                            votes[lang].append((lid, vote_score))

        decision = Counter()  # for each language key we have a score
        for lang in votes:
            decision[lang] = sum(
                vote_score
                for (_, vote_score)
                in votes[lang])

        return decision

    def decide_language(self) -> None:
        # total_orig_support_ratio = self.collection_json_stats["total_orig_support_ratio"]
        # article_len_thresh = self.args.threshold_langident_trust

        # we destructively modify the dictionary
        for oldjinfo in self.next_contentitem():
            log.info(f"WORKING ON {oldjinfo['id']} {oldjinfo}")
            jinfo = {}

            # copy relevant attributes from stage 1 individual file
            for attr in self.attrs_from_content_item:
                jinfo[attr] = copy.copy(oldjinfo.get(attr))

            trust_orig_lg = False
            if self.collection_stats.get("overall_orig_lg_support"):
                trust_orig_lg = self.collection_stats["overall_orig_lg_support"] > 0.75

            dominant_lg = self.collection_stats["dominant_language"]
            jinfo.update({
                "version": __version__,
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(sep="T", timespec="seconds")
            })
            if jinfo["tp"] == "img":
                self.results.append(jinfo)
                continue

            # rule 1
            if not trust_orig_lg or not oldjinfo.get("orig_lg"):
                oldjinfo["orig_lg"] = None
                self.lids.discard("orig_lg")
            else:
                # turn it into a "normal" system output with [{"lang":LANG, "prob":PROB}]
                orig_lg_support = self.collection_stats["lg_support"]["orig_lg"][oldjinfo["orig_lg"]]
                oldjinfo["orig_lg"] = [{"lang": oldjinfo["orig_lg"],
                                        "prob": 1.0*orig_lg_support}]

            # rule 2
            all_lid_preds = self.get_best_lid(oldjinfo)
            # print('al_lid_preds',all_lid_preds) # {'langdetect': {'lang': 'de', 'prob': 1.0}, 'wp_ft': {'lang': 'de', 'prob': 0.99}, 'langid': {'lang': 'de', 'prob': 1.0}, 'orig_lg': 'd', 'impresso_ft': {'lang': 'de', 'prob': 1.0}}
            # print(all_lid_preds.values())
            all_lid_languages = set(all_lid_preds[lid]["lang"] for lid in all_lid_preds)
            if len(all_lid_languages) == 1:
                jinfo["lg"] = min(all_lid_languages)
                jinfo["lg_decision"] = "all"
                self.decision_distribution['all'] += 1
                self.results.append(jinfo)
                continue

            all_but_impresso_ft_lid_languages = set(
                all_lid_preds[lid]["lang"] for lid in all_lid_preds if lid != "impresso_ft")
            if len(all_but_impresso_ft_lid_languages) == 1:
                other_lg = min(all_but_impresso_ft_lid_languages)
                if other_lg not in {"de", "fr"}:
                    jinfo["lg"] = other_lg
                    jinfo["lg_decision"] = "other"
                    self.decision_distribution['other'] += 1
                    self.results.append(jinfo)
                    continue
            if jinfo["len"] < 50:  # or jinfo.get("alphabetical_ratio"] < 0.1:
                jinfo["lg"] = dominant_lg
                jinfo["lg_decision"] = "dominant-by-len"
                self.decision_distribution["dominant-by-len"] += 1
                self.results.append(jinfo)
                continue
            votes = self.get_votes(oldjinfo)
            log.debug(f"VOTES\t{votes}")
            jinfo["lg"] = votes.most_common(n=1)[0][0]
            jinfo["lg_decision"] = "voting"
            self.decision_distribution["voting"] += 1
            self.results.append(jinfo)
            log.critical(f"VOTES={votes} {jinfo}")
            #log.critical(f"Case not implemented {oldjinfo}")
            self.decision_distribution['voting'] += 1

        log.critical(f"DECISIONS {self.decision_distribution}")


if __name__ == '__main__':
    import argparse

    description = "Classify language of impresso content item given all collected evidence"
    epilog = ""
    parser = argparse.ArgumentParser(description=description, epilog=epilog)
    parser.add_argument('-l', '--logfile', dest='logfile',
                        help='write log to FILE', metavar='FILE')
    parser.add_argument('-v', '--verbose', dest='verbose', default=2, type=int, metavar="LEVEL",
                        help='set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)')
    parser.add_argument('-C', '--collection-json-stats', dest='collection_json_stats', type=str,
                        help='collection statistics JSON (default %(default)s)')
    parser.add_argument('-T', '--threshold_langident_trust', dest='threshold_langident_trust', default=100, type=int,
                        help='threshold on article length in chars for trusting automatic language identification (default %(default)s)')
    parser.add_argument('-i', '--input-file', default="/dev/stdin",
                        help="path to input file from s3 batch, json format")
    parser.add_argument('-o', '--output-file', default="/dev/stdout",
                        help="path to folder where processed .json files should be saved")
    parser.add_argument('--boost-factor', dest='boost_factor', metavar="B", default=1.5,
                        type=float,
                        help='Boost factor for boosted lids (default %(default)s)')
    parser.add_argument('--minimal-lid-probability', dest='minimal_lid_probability', metavar="P", default=0.5,
                        type=float,
                        help='Minimal probability for a LID decision to be considered a vote (default %(default)s)')
    parser.add_argument('-m', '--minimal-text-length', default=20, type=int,
                        help="minimal text length of content items to apply automatic landuage identification (default %(default)s)")
    parser.add_argument(
        "--lids",
        nargs="+",
        default=[],
        metavar='LID',
        help="Names of all LID systems (e.g. langdetect, langid) to use. Do not add orig_lg here!",
    )

    parser.add_argument(
        "--boosted-lids",
        nargs="+",
        default=[],
        metavar="LID",
        help="Subset of LID systems or orig_lg that are boosted by a factor if they have support from any other"
             "system or orig_lg.",
    )
    parser.add_argument(
        "--double-boosted-lids",
        nargs="+",
        default=[],
        metavar="LID",
        help="Subset of boosted lid systems that get a double boost due to their superior quality on the data"
    )

    parser.add_argument(
        "--admissible-languages",
        nargs="+",
        default=None,
        metavar="L",
        help="""Names of 
        (default: %(default)s)""",
    )

    args = parser.parse_args()

    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
                  logging.INFO, logging.DEBUG]
    logging.basicConfig(level=log_levels[args.verbose],
                        format='%(asctime)-15s %(levelname)s: %(message)s')

    # launching application ...
    MainApplication(args).run()
