#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""
Collect the content item metadata and automatic LID results per newspaper.

Motivation:
Sometimes the metadata about language of the content items is
 - missing (e.g. often for advertisements, or just completely non-existing by certain providers
 - wrong (sometimes only a fraction of the content items, sometimes the majority)
 - correct

We therefore use the automatic LID to achieve the following goals:
 - establish confidence values into the provided original language classes
 - establish a majority default language of a collection in order to classify content items with very short text
 items  where automatic LID is not reliable

In order to compute the support for a certain language per content item, we rely on the ensemble opinion of the
original language information and the automatic LID results.
The following strategy is applied to decide for a language:

 - we restrict the language support computation to content items of minimal length (typically the minimal length
 where automatic LID was applied)
 - every automatic LID prediction and the original language orig_lg (if existing) have 1 vote
 - the "impresso_ft" and "orig_lg" are more trustworthy than the other LID predictions. If their prediction is shared
 any other system, their vote is upweighted by a factor of 1.5



The output of the first stage, where a bunch of automatic language identifiers is applied, looks like:

{"tp": "page", "cid": "arbeitgeber-1909-01-02-a-i0017", "len": 5636, "orig_lg": null, "alphabetical_ratio": 0.79, "langdetect": [{"lang": "de", "prob": 1.0}], "langid": [{"lang": "de", "prob": 1.0}], "impresso_ft": [{"lang": "de", "prob": 1.0}], "wp_ft": [{"lang": "de", "prob": 0.95}, {"lang": "en", "prob": 0.01}]}

{"tp": "tb", "cid": "luxzeit1858-1859-01-07-a-i0020", "len": 143, "orig_lg": null, "alphabetical_ratio": 0.38, "langdetect": [{"lang": "fr", "prob": 1.0}], "langid": [{"lang": "fr", "prob": 1.0}], "impresso_ft": [{"lang": "fr", "prob": 1.0}], "wp_ft": [{"lang": "fr", "prob": 0.98}]}
{"tp": "tb", "cid": "luxzeit1858-1859-01-07-a-i0021", "len": 20, "orig_lg": null}



Mode: overall analysis:
 - create JSON file at collection level containing the following information:
    - collection: collection name
    - textual_content_item_count: int
    - textual_content_item_with_orig_lg_count: int
    - total_orig_support_ratio: float
    - orig_lg_support_total: Boolean
    - orig_lg_support_distribution: list of lang/prob



The intended output looks for instance as:
{
  "collection": "BDC",
  "total_orig_support_ratio": 1,
  "textual_content_item_with_orig_lg_count": 145,
  "textual_content_item_count": 145,
  "orig_lg_support": {
    "fr": 127
  },
  "orig_lg": {
    "fr": 145
  },
  "orig_lg_threshold": {
    "fr": 127
  },
  "langidlangdetect": {
    "fr": 127,
    "null": 18
  },
  "contentitem_type_distribution": {
    "ar": 135,
    "img": 1,
    "ad": 10
  },
  "threshold_for_support": 200,
  "dominant_orig_lg": [
    {
      "lang": "fr",
      "count": 145
    }
  ],
  "dominant_langidlangdetect": [
    {
      "lang": "fr",
      "count": 127
    }
  ]
}


"""

import json
import datetime
import logging
from collections import Counter, defaultdict
from typing import Optional, Set, Iterable

from smart_open import open

log = logging.getLogger(__name__)
__VERSION__ = "2020.11.29"


def update_relfreq(counter: Counter, n: Optional[int] = None, ndigits: int = 9) -> None:
    """Update the frequency distribution counter into relative frequency

    :param ndigits: Round floats to n digits
    :param n: Total of counts if given
    :type counter: Some frequency distribution
    """

    if n is None:
        n = sum(counter.values())
    for k in counter:
        counter[k] = round(counter[k] / n, ndigits)


class MainApplication(object):

    def __init__(self, args):

        self.version = __VERSION__
        """Version of the collection script"""

        # self.ts => see self.ts()

        self.args = args
        """Command line arguments"""

        self.attrs_for_json = ["collection",
                               "dominant_language",
                               "overall_orig_lg_support",
                               "n",
                               "lid_distributions",
                               "contentitem_type_distribution",
                               "lids",
                               "boosted_lids",
                               "boost_factor",
                               "admissible_languages",
                               "version",
                               "ts"
                               ]
        """Defines all attributes of the this object that enter the JSON output in the corresponding order"""

        self.lids: Set[str] = set(lid for lid in self.args.lids if lid != "orig_lg")

        if len(self.lids) < 1:
            print(f"ERROR: At least one language identificator needed")
            exit(2)

        self.total_orig_support_ratio: Optional[float] = None
        """Percentage of all content items with a non-null original language and the requested minimal length 
        threshold that agree with the ensemble decision"""

        self.boosted_lids: Set[str] = set(lid for lid in self.args.boosted_lids if lid == "orig_lg" or lid in self.lids)
        """Set of LIDs that are boosted by a boost factor
        """
        if self.boosted_lids != set(self.args.boosted_lids):
            log.warning(
                f"The set of boosted_lids contained the following invalid and ignored system identifiers: "
                f"{self.boosted_lids.symmetric_difference(self.args.boosted_lids)}")

        self.boost_factor: float = self.args.boost_factor
        """Boost factor applied to boosted LIDS if the have support from at least another LID
        
        The idea is that on their own some of the LIDs or the orig_lg can be pretty wrong. If they have at least a 
        support from a another system the confidence into their decision grows considerably.
        """

        self.overall_orig_lg_support: Optional[float] = None
        """Percentage of existing language categorizations ("orig_lg" attribute) that has been backed by the ensemble 
        decision. This number serves as a criterion on the confidence that can establish for a collection.
        """

        self.n: int = 0
        """Total number of content items that are not filtered out due to incompatible type (img) or shortness"""

        self.dominant_language: Optional[str] = None
        """The most frequent language according to the ensemble decisions. 
        
        The detailed percentage for this language 
        can be found in the language class distribution in the ensemble frequency distribution. This value is 
        extracted for convenience here. """

        self.admissible_languages: Optional[Set[str]] = \
            set(args.admissible_languages) if args.admissible_languages else None
        """Set of admissible language: If None, no restrictions are applied"""

        self.orig_lg_support: Counter = Counter()
        """Counter for agreement/disagreement of ensemble decision and original language
        
        The overall_orig_lg_support is computed from this distribution.
        """

        self.lid_distributions: dict = {lid: Counter() for lid in self.lids}
        """Dictionary with a language frequency distribution for each selected LID, `orig_lg` and the voting result 
        as `ensemble``
        
        Properties of standard LIDs used in impresso:
            - langid LID (recognizes many language, incl. lb)
            - langdetect LID (recognizes many languages, except lb)
            - impresso_ft impresso model based on fasttext (supports fr/de/lb)
            - wp_ft wikipedia model delivered by fasttext (supports many languages, incl. lb)
        """

        self.lid_distributions['orig_lg']: Counter = Counter()
        """Distribution of provided original languages"""

        self.lid_distributions['ensemble']: Counter = Counter()
        """Distribution of ensemble decisions """

        self.contentitem_type_distribution: Counter = Counter()
        """Distribution of content item types"""

        self.content_length_stats: Counter = Counter()
        """Distribution of article lengths (raw character counts)"""

        self.collection: str = self.args.collection
        """Short canonical name of newspaper"""

    @property
    def ts(self):
        """Return ISO timestamp in impresso resolution"""

        return datetime.datetime.now(datetime.timezone.utc).isoformat(sep="T", timespec="seconds")

    def run(self):
        """Run the application"""

        self.process()

    def get_next_contentitem(self) -> Iterable[dict]:
        """
        Yield each content item

        :rtype: object
        """
        with open(self.args.infile) as infile:
            for line in infile:
                contentitem = json.loads(line)
                yield contentitem

    def update_lid_counters(self, content_item: dict) -> None:
        """Update the self.lid_counter structure with the most probable language"""

        for lid in self.lids:
            if lid in content_item and content_item[lid] is not None and len(content_item[lid]) > 0:
                lang = content_item[lid][0]['lang']
                self.lid_distributions[lid][lang] += 1

    def get_votes(self, content_item: dict) -> Optional[Counter]:
        """Return dictionary with boosted votes per language"""

        votes = defaultdict(list)  # for each language key we have a list of tuples (LID, vote_score)
        if content_item.get('orig_lg'):
            votes[content_item.get('orig_lg')].append(('orig_lg', (1 if 'orig_lg' not in self.boosted_lids else
                                                                   self.boost_factor)))
        for lid in self.lids:
            if lid in content_item and content_item[lid] is not None and len(content_item[lid]) > 0:
                lang, prob = content_item.get(lid)[0]["lang"], content_item.get(lid)[0]["prob"]
                if self.admissible_languages is None or lang in self.admissible_languages:
                    if prob >= self.args.minimal_lid_probability:
                        votes[lang].append((lid, (1 if lid not in self.boosted_lids else self.boost_factor)))

        decision = Counter()  # for each language key we have a score
        for lang in votes:
            decision[lang] = sum(
                1 * (boost if len(votes[lang]) > 1 else 1)
                for (_, boost)
                in votes[lang])
            if decision[lang] < self.args.minimal_vote_score:
                del decision[lang]

        log.debug(f"{decision if len(decision) > 0 else None} "
                  f"votes = {dict(votes)} decision-distro {decision} decision = "
                  f"content_item ={content_item}")

        if len(decision) < 1:  # no decision taken
            return None

        return decision

    def collect_statistics(self) -> None:
        """Collect and update statistics in self"""

        for ci in self.get_next_contentitem():
            if self.collection is None:
                self.collection = ci["cid"][0:len(ci["cid"]) - 19]  #
                log.warning(f"WARNING: Inferrd collection name from first content item as '{self.collection}'")
            self.contentitem_type_distribution[ci.get("tp")] += 1
            if ci["tp"] == "img":
                continue
            ci_len = ci.get("len", 0)
            self.content_length_stats[ci_len] += 1
            if ci_len < self.args.threshold_for_support:
                log.warning(f"WARNING-SHORT-CONTENTITEM {ci['id']}\t{ci.get('len', 0)}")
                continue
            self.n += 1
            self.update_lid_counters(ci)
            orig_lg = ci.get("orig_lg")
            self.lid_distributions['orig_lg'][orig_lg] += 1

            decision = self.get_votes(ci)
            if decision is None:
                lang = None

            else:
                lang, score = decision.most_common(1)[0]
                if len(decision) > 1 and decision.most_common(2)[1][1] == score:
                    log.warning(f"SCORE-TIE in decision {decision}")
                    lang = None
                log.debug(f"lang={lang} score={score}")

            self.lid_distributions['ensemble'][lang] += 1

            if orig_lg is not None:
                if lang == orig_lg:
                    self.orig_lg_support[lang] += 1
        try:
            self.overall_orig_lg_support = \
                round(sum(self.orig_lg_support.values())
                      / sum(self.lid_distributions['orig_lg'][key]
                            for
                            key in self.lid_distributions['orig_lg'] if
                            key is not
                            None), self.args.round_ndigits)
        except ZeroDivisionError:
            self.overall_orig_lg_support = None
        for lid in self.lid_distributions:
            update_relfreq(self.lid_distributions[lid], n=self.n, ndigits=self.args.round_ndigits)

        self.dominant_language = self.lid_distributions['ensemble'].most_common(1)[0][0]

    def jsonify(self) -> dict:
        """Return JSON string representation of relevant statistics"""

        json_data = {}
        for attr in self.attrs_for_json:
            json_data[attr] = getattr(self, attr)
            if type(json_data[attr]) == set:
                json_data[attr] = list(json_data[attr])

        return json_data

    def process(self):
        """Process all jsonl entries"""

        self.collect_statistics()
        json_data = self.jsonify()
        print(json.dumps(json_data))


if __name__ == '__main__':
    import argparse

    description = "Aggregate language-related statistics on content items."
    epilog = ""
    parser = argparse.ArgumentParser(description=description, epilog=epilog)
    parser.add_argument('-l', '--logfile', dest='logfile',
                        help='write log to FILE', metavar='FILE')
    parser.add_argument('-v', '--verbose', dest='verbose', default=2, type=int, metavar="LEVEL",
                        help='set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)')
    parser.add_argument('--collection', dest='collection', type=str,
                        help='collection name for statistics output (default %(default)s)')
    parser.add_argument('--threshold_for_support', dest='threshold_for_support', metavar="n", default=200,
                        type=int,
                        help='threshold on article length in chars for computing orig_lg support (default %(default)s)')
    parser.add_argument('--boost_factor', dest='boost_factor', metavar="B", default=1.5,
                        type=float,
                        help='Boost factor for boosted lids (default %(default)s)')
    parser.add_argument('--minimal_lid_probability', dest='minimal_lid_probability', metavar="P", default=0.25,
                        type=float,
                        help='Minimal probability for a LID decision to be considered a vote (default %(default)s)')
    parser.add_argument('--minimal_vote_score', dest='minimal_vote_score', metavar="S", default=1.5,
                        type=float,
                        help='Minimal vote score from ensemble to reach a decision (default %(default)s)')
    parser.add_argument('--round_ndigits', dest='round_ndigits', default=9,
                        type=int,
                        help='round floats in the output to n digits (default %(default)s)')

    parser.add_argument(
        "infile",
        metavar="INPUT",
        help="Input file (default: STDIN)",
    )
    parser.add_argument(
        "--lids",
        nargs="+",
        default=[],
        metavar='LID',
        help="Names of all LID systems (e.g. langdetect, langid) to use. Do not add orig_lg here!",
    )
    parser.add_argument(
        "--boosted_lids",
        nargs="+",
        default=[],
        metavar="LID",
        help="Subset of LID systems or orig_lg that are boosted by a factor if they have support from any other "
             "system or orig_lg.",
    )
    parser.add_argument(
        "--admissible_languages",
        nargs="+",
        default=None,
        metavar="L",
        help="""Names of 
        (default: %(default)s)""",
    )

    arguments = parser.parse_args()

    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
                  logging.INFO, logging.DEBUG]
    logging.basicConfig(level=log_levels[arguments.verbose],
                        format='%(asctime)-15s %(levelname)s: %(message)s')

    MainApplication(arguments).run()
