#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
Classify language of impresso content item given all collected evidence from various sources

This script takes two JSON files as input, one with information per content item
and the other with global statistics.


Example JSON with LID predictions per content item:
    {
       "tp":"page",
       "id":"arbeitgeber-1909-01-02-a-i0017",
       "len":5636,
       "orig_lg":null,
       "alphabetical_ratio":0.79,
       "langdetect": [{"lang": "de", "prob": 1.0}],
       "langid": [{"lang": "de", "prob": 1.0}],
       "impresso_ft": [{"lang": "de", "prob": 1.0}],
       "wp_ft": [{"lang": "de", "prob": 0.95}, {"lang": "en", "prob": 0.01}]}
    }

Example JSON with aggregated statistics per collection:
    {
      "collection": "waeschfra",
      "total_orig_support_ratio": 0.828916455220374,
      "textual_content_item_with_orig_lg_count": 14373,
      "textual_content_item_count": 14587,
      "orig_lg_support": {"de": 7990.5, "fr": 830 },
      "orig_lg": {"de": 11511, "fr": 2862, "null": 214},
      "orig_lg_threshold": {"de": 8487, "fr": 2154},
      "langidlangdetect": {"de": 7731, "null": 6066, "fr": 785, "nl": 4, "it": 1},
      "contentitem_type": {"ar": 14373, "img": 5366, "ad": 210, "ob": 1, "tb": 3},
      "threshold_for_support": 200,
      "dominant_orig_lg": [{"lang": "de", "count": 11511}, {"lang": "fr", "count": 2862}],
      "dominant_langidlangdetect": [{"lang": "de", "count": 7731},{"lang": "fr", "count": 785}]
    }

"""

__version__ = "2020.12.08"

import copy
import datetime
import json
import logging
from collections import Counter, defaultdict
from typing import Iterable, Optional, Set, List

import jsonlines
from smart_open import open

log = logging.getLogger(__name__)


def read_json(path: str) -> dict:
    """Read a JSON file.

    :param str path: Path to JSON file.
    :return: Content of the JSON file.
    :rtype: dict

    """

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ImpressoLanguageIdentifier(object):
    """Identify language for each content item using ensemble decision

    :param str infile: JSON file with language predictions per content item.
    :param str outfile: Path to folder where processed JSON files should be saved.
    :param str collection_stats_filename: JSON file with aggregated statistics per collection. Read in into the attribute collection_stats
    :param Set[str] lids: Set of LID systems predict to language/probability pairs.
        Therefore, orig_lg is not seen as LID system as it "predicts" only a single language if any.
    :param float weight_lb_impresso_ft: voting weight for impresso_ft predicting Luxembourgish.
    :param float minimal_lid_probability: Minimal probability for a LID decision to be considered a vote.
    :param int minimal_text_length: threshold for text length in characters to apply automatic language identification.
    :param float minimal_voting_score: minimal vote score for voting decision to be accepted
    :param float threshold_confidence_orig_lg: Ignore original language information when below this confidence threshold.
    :param Optional[Set[str]] admissible_languages: Limit languages in the ensemble decisions.
        If None, no restrictions are applied.

    :attr str version: Version of the collection script.
    :attr list attrs_for_json: Defines list of attributes to copy over from stage 1 content items' JSON.
    :attr Counter decision_distribution: Distribution over rules to predict a language.
    :attr list results: Collection of content items with their identified language.

    """

    def __init__(
        self,
        infile: str,
        outfile: str,
        collection_stats_filename: str,
        lids: Set[str],
        weight_lb_impresso_ft: float,
        minimal_lid_probability: float,
        minimal_text_length: int,
        minimal_voting_score: float,
        threshold_confidence_orig_lg: float,
        admissible_languages: Optional[Set[str]],
    ):

        self.version: str = __version__

        self.attrs_from_content_item: list = [
            "id",
            "tp",
            "len",
            "orig_lg",
            "alphabetical_ratio",
        ]

        self.infile: str = infile

        self.outfile: str = outfile

        self.lids: Set[str] = set(lid for lid in lids if lid != "orig_lg")

        if len(self.lids) < 1:
            log.error(
                "No LID models provided. At least one language identificator needed."
            )
            exit(2)

        self.weight_lb_impresso_ft: float = weight_lb_impresso_ft

        self.admissible_languages: Optional[Set[str]] = (
            set(admissible_languages) if admissible_languages else None
        )

        self.threshold_confidence_orig_lg: float = threshold_confidence_orig_lg
        self.minimal_lid_probability: float = minimal_lid_probability
        self.minimal_text_length: float = minimal_text_length
        self.minimal_voting_score: float = minimal_voting_score

        self.decision_distribution: Counter = Counter()
        self.collection_stats: dict = read_json(collection_stats_filename)
        self.results: List[dict] = []

    def run(self):
        self.classify_language_per_item()
        self.write_output()

    def write_output(self):
        with open(self.outfile, mode="w", encoding="utf-8") as of:
            writer = jsonlines.Writer(of)
            writer.write_all(self.results)

    def next_contentitem(self) -> Iterable[dict]:
        with open(self.infile, encoding="utf-8") as reader:
            json_reader = jsonlines.Reader(reader)
            for jdata in json_reader:
                yield jdata

    def get_best_lid(self, jinfo: dict):
        """
        Use only the top prediction per LID
        """
        result = {}
        for lid_system in self.lids:
            lid_preds = jinfo.get(lid_system)
            if lid_preds and len(lid_preds) > 0:
                result[lid_system] = lid_preds[0]
        return result

    def get_votes(self, content_item: dict) -> Optional[Counter]:
        """Return dictionary with weighted votes per language"""

        # for each language key we have a list of tuples (LID, vote_score)
        votes = defaultdict(list)

        for lid in self.lids:

            # filter on LID systems
            if (
                lid in content_item
                and content_item[lid] is not None
                and len(content_item[lid]) > 0
            ):
                lang, prob = content_item[lid][0]["lang"], content_item[lid][0]["prob"]
                # filter on languages
                if (
                    self.admissible_languages is None
                    or lang in self.admissible_languages
                ):
                    # filter on probability
                    if prob >= self.minimal_lid_probability:
                        lang_support = (
                            self.collection_stats["lg_support"][lid].get(lang) or 0.0
                        )

                        # weight vote on trustworthiness of a LID predicting a particular language
                        if lang_support:
                            vote_score = prob * lang_support

                            # special weight for impresso_ft when predicting Luxembourgish
                            if lid == "impresso_ft" and lang == "lb":
                                vote_score *= self.weight_lb_impresso_ft

                            votes[lang].append((lid, vote_score))

        # for each language key we have a score
        decision = Counter()

        for lang in votes:
            decision[lang] = sum(vote_score for (_, vote_score) in votes[lang])

        return decision

    def classify_language_per_item(self) -> None:

        # we destructively modify the dictionary
        for old_jinfo in self.next_contentitem():
            log.info(f"Process {old_jinfo['id']}")
            jinfo = {}

            jinfo.update(
                {
                    "version": __version__,
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(
                        sep="T", timespec="seconds"
                    ),
                }
            )
            # copy relevant attributes from stage 1 for each content item
            for attr in self.attrs_from_content_item:
                jinfo[attr] = copy.copy(old_jinfo.get(attr))

            if jinfo["tp"] == "img":
                self.results.append(jinfo)
                continue

            trust_orig_lg = False
            if self.collection_stats.get("overall_orig_lg_support"):
                trust_orig_lg = (
                    self.collection_stats["overall_orig_lg_support"]
                    > self.threshold_confidence_orig_lg
                )

            dominant_lg = self.collection_stats["dominant_language"]

            # rule 1: ignore original language information when not trustworthy
            if not trust_orig_lg or not old_jinfo.get("orig_lg"):
                old_jinfo["orig_lg"] = None
                self.lids.discard("orig_lg")
            else:
                # set confidence value of original language information as probability
                # the original probability was always 1 before
                orig_lg_support = self.collection_stats["lg_support"]["orig_lg"][
                    old_jinfo["orig_lg"]
                ]
                # use the original language information only
                old_jinfo["orig_lg"] = [
                    {"lang": old_jinfo["orig_lg"], "prob": orig_lg_support}
                ]

            # rule 2
            all_lid_preds = self.get_best_lid(old_jinfo)
            all_lid_languages = set(all_lid_preds[lid]["lang"] for lid in all_lid_preds)

            # rule 2a: follow unequivocal predictions
            if len(all_lid_languages) == 1:
                jinfo["lg"] = min(all_lid_languages)
                jinfo["lg_decision"] = "all"
                self.decision_distribution["all"] += 1
                self.results.append(jinfo)
                continue

            all_but_impresso_ft_lid_languages = set(
                all_lid_preds[lid]["lang"]
                for lid in all_lid_preds
                if lid != "impresso_ft"
            )

            # rule 2b: off-the-shelf LID agree on language other than DE or FR
            if len(all_but_impresso_ft_lid_languages) == 1:
                other_lg = min(all_but_impresso_ft_lid_languages)
                if other_lg not in {"de", "fr"}:
                    jinfo["lg"] = other_lg
                    jinfo["lg_decision"] = "other"
                    self.decision_distribution["other"] += 1
                    self.results.append(jinfo)
                    continue

            # rule 2c: set dominant language of collection for very short articles
            if jinfo["len"] < self.minimal_text_length:
                jinfo["lg"] = dominant_lg
                jinfo["lg_decision"] = "dominant-by-len"
                self.decision_distribution["dominant-by-len"] += 1
                self.results.append(jinfo)
                continue

            votes = self.get_votes(old_jinfo)
            log.debug(f"VOTES={votes} {jinfo}")
            if log.level == 10:
                jinfo["votes"] = votes.most_common()
            if len(votes) < 1 or (len(votes) > 1 and votes.most_common(n=1)[0][1] < self.minimal_voting_score):
                jinfo["lg"] = dominant_lg
                jinfo["lg_decision"] = "dominant-by-lowvote"
                self.decision_distribution["dominant-by-lowvote"] += 1
                self.results.append(jinfo)
                continue

            # rule 3: get decision by ensemble voting for less obvious cases
            jinfo["lg"] = votes.most_common(n=1)[0][0]
            jinfo["lg_decision"] = "voting"
            self.decision_distribution["voting"] += 1
            self.results.append(jinfo)

        log.critical(f"DECISIONS {self.decision_distribution}")


if __name__ == "__main__":
    import argparse

    DESCRIPTION = (
        "Classify language of impresso content items given all collected evidence"
    )

    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("-l", "--logfile", help="write log to FILE", metavar="FILE")
    parser.add_argument(
        "-v",
        "--verbose",
        default=2,
        type=int,
        metavar="LEVEL",
        help="set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)",
    )
    parser.add_argument(
        "-C",
        "--collection-stats-filename",
        type=str,
        help="collection statistics JSON file (default %(default)s)",
    )

    parser.add_argument(
        "--threshold_confidence_orig_lg",
        default=0.75,
        type=float,
        help="ignore original language information when below this confidence threshold (default %(default)s)",
    )

    parser.add_argument(
        "-i",
        "--infile",
        default="/dev/stdin",
        help="path to input file from s3 batch, json format",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        default="/dev/stdout",
        help="path to folder where processed .json files should be saved",
    )
    parser.add_argument(
        "--weight-lb-impresso-ft",
        metavar="W",
        default=3,
        type=float,
        help="special voting weight for impresso_ft predicting Luxembourgish (default %(default)s)",
    )
    parser.add_argument(
        "--minimal-lid-probability",
        metavar="P",
        default=0.5,
        type=float,
        help="minimal probability for a LID decision to be considered a vote (default %(default)s)",
    )
    parser.add_argument(
        "--minimal-voting-score",
        metavar="W",
        default=0.5,
        type=float,
        help="minimal vote score for voting decision to be accepted (default %(default)s)",
    )

    parser.add_argument(
        "-m",
        "--minimal-text-length",
        default=20,
        type=int,
        help="minimal text length of content items to apply automatic language identification (default %(default)s)",
    )
    parser.add_argument(
        "--lids",
        nargs="+",
        default=[],
        metavar="LID",
        help="names of all LID systems (e.g. langdetect, langid) to use. Do not add orig_lg here!",
    )
    parser.add_argument(
        "--admissible-languages",
        nargs="+",
        default=None,
        metavar="L",
        help="Names of languages considered in the ensemble decisions. "
        "If None, no restrictions are applied (default: %(default)s)",
    )

    arguments = parser.parse_args()

    log_levels = [
        logging.CRITICAL,
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
    ]
    logging.basicConfig(
        level=log_levels[arguments.verbose],
        format="%(asctime)-15s %(levelname)s: %(message)s",
    )
    language_identifier_args = {
        "infile",
        "outfile",
        "collection_stats_filename",
        "lids",
        "weight_lb_impresso_ft",
        "minimal_lid_probability",
        "minimal_text_length",
        "threshold_confidence_orig_lg",
        "minimal_voting_score",
        "admissible_languages",
    }
    # launching application ...
    ImpressoLanguageIdentifier(
        **{k: v for k, v in vars(arguments).items() if k in language_identifier_args}
    ).run()
