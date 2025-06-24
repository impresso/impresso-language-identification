#!/usr/bin/env python3

"""
Determine the language of a impresso content item given all collected evidence from
various systems

This script takes two intermediate JSON files as input, one with information per content
item and the other with global statistics.

"""

__version__ = "2025.06.24"

import copy
import datetime
import json
import logging
import sys
from collections import Counter, defaultdict
from typing import DefaultDict, Iterable, List, Optional, Set

import jsonlines
import jsonschema
import smart_open

log = logging.getLogger(__name__)


def read_json(path: str) -> dict:
    """Read a JSON file.

    :param str path: Path to JSON file.
    :return: Content of the JSON file.
    :rtype: dict

    """

    with smart_open.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ImpressoLanguageIdentifier(object):
    """Identify language for each content item using ensemble decision

    :param str infile: JSON file with language predictions per content item.
    :param str outfile: Path to folder where processed JSON files should be saved.
    :param str collection_stats_filename: JSON file with aggregated statistics per
        collection. Read in into the attribute collection_stats
    :param Set[str] lids: Set of LID systems predict to language/probability pairs.
        Therefore, orig_lg is not seen as LID system as it "predicts" only a single
        language if any.
    :param float weight_lb_impresso_ft: voting weight for impresso_ft predicting
        Luxembourgish.
    :param float minimal_lid_probability: Minimal probability for a LID decision to be
        considered a vote.
    :param int minimal_text_length: threshold for text length in characters to apply
        automatic language identification.
    :param float minimal_voting_score: minimal vote score for voting decision to be
        accepted
    :param float threshold_confidence_orig_lg: Ignore original language information when
        below this confidence threshold.
    :param Optional[Set[str]] admissible_languages: Limit languages in the ensemble
        decisions. If None, no restrictions are applied.
    :param Optional[str] diagnostics_json: Filename for diagnostics
    :param bool validate: Validate final lang identification JSON against schema
    :param str git_describe: Output of git describe to use as version if not empty
        string

    :attr list attrs_per_content_item: Defines order of attributes and list of
        attributes to copy over from stage 1 content items' JSON and nullable attributes
        from stage 2
    :attr DefaultDict[Counter] stats: Distribution for any JSON property of interest
        (given as key)
    :attr list results: Collection of content items with their identified language.
    :attr dict schema: JSON schema for the output JSON
    :attr method schema_validator: JSON schema validator
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
        diagnostics_json: Optional[str],
        validate: bool,
        git_describe: str,
        alphabetical_ratio_threshold: Optional[float] = None,
    ):

        self.git_describe: str = git_describe

        self.diagnostics_json: str = diagnostics_json

        self.lids: Set[str] = set(lid for lid in lids if lid != "orig_lg")

        self.attrs_per_content_item: list = (
            [
                {"key": "id", "required": True, "source": "language_identifier"},
                {"key": "lg", "required": True},
                {"key": "lg_decision", "required": False},
                {"key": "tp", "required": True, "source": "language_identifier"},
                {"key": "len", "required": True, "source": "language_identifier"},
                {"key": "orig_lg", "required": True, "source": "language_identifier"},
                {
                    "key": "alphabetical_ratio",
                    "required": False,
                    "source": "language_identifier",
                },
                {
                    "key": "impresso_language_identifier_version",
                    "required": False,
                },
                {
                    "key": "language_identifier_version",
                    "required": False,
                    "source": "language_identifier",
                },
                {"key": "year", "required": False},
                {"key": "collection", "required": False},
            ]
            + [
                {"key": k, "required": False, "source": "language_identifier"}
                for k in sorted(self.lids)
            ]
            + [{"key": "votes", "required": False}]
        )

        self.infile: str = infile

        self.outfile: str = outfile

        if len(self.lids) < 1:
            log.error("No LID specified. At least one language identificator  needed.")
            sys.exit(2)

        self.weight_lb_impresso_ft: float = weight_lb_impresso_ft

        self.admissible_languages: Optional[Set[str]] = (
            set(admissible_languages) if admissible_languages else None
        )

        self.threshold_confidence_orig_lg: float = threshold_confidence_orig_lg
        self.minimal_lid_probability: float = minimal_lid_probability
        self.minimal_text_length: float = minimal_text_length
        self.minimal_voting_score: float = minimal_voting_score
        self.alphabetical_ratio_threshold: float = alphabetical_ratio_threshold or 0.0

        self.schema: Optional[dict] = None
        self.schema_validator: Optional[jsonschema.validators.Draft6Validator] = None
        self.stats: DefaultDict[str, Counter] = defaultdict(Counter)
        self.stats_keys: List[str] = ["lg", "orig_lg", "tp", "lg_decision"]
        self.collection_stats: dict = read_json(collection_stats_filename)
        self.results: List[dict] = []

        self.validate: bool = validate
        if self.validate:
            self.load_schema()

    def run(self):
        """Run the application"""

        self.update_impresso_lid_results()
        self.write_output()
        self.update_stats()
        self.write_diagnostics()

    def load_schema(self) -> None:
        """
        Load the JSON schema for language identification.

        This method fetches the schema from the specified URL and creates a
        Draft6Validator for it. The schema and the validator are stored as instance
        variables for later use.

        Raises:
            jsonschema.exceptions.SchemaError: If the provided schema is not valid.
            jsonschema.exceptions.RefResolutionError: If the provided schema contains an
            unresolvable JSON reference.
        """
        base_uri = (
            "https://impresso.github.io/impresso-schemas/json/language_identification/"
        )
        schema_file = "language_identification.schema.json"

        with smart_open.open(
            base_uri + schema_file,
            "r",
        ) as f:
            self.schema = json.load(f)

        resolver = jsonschema.RefResolver(
            referrer=self.schema,
            base_uri=base_uri,
        )
        self.schema_validator = jsonschema.Draft6Validator(
            schema=self.schema,
            resolver=resolver,
        )

    def write_output(self) -> None:
        """Write JSONlines output"""

        with smart_open.open(self.outfile, mode="w", encoding="utf-8") as of:
            writer = jsonlines.Writer(of)
            writer.write_all(self.results)

    def write_diagnostics(self) -> None:
        """Write JSON diagnostics with per-collectio stats"""

        with smart_open.open(self.diagnostics_json, mode="w", encoding="utf-8") as of:
            print(json.dumps(self.stats), file=of)

    def next_content_item(self) -> Iterable[dict]:
        """Yield next content item"""

        with smart_open.open(self.infile, mode="r", encoding="utf-8") as reader:
            json_reader = jsonlines.Reader(reader)
            for jdata in json_reader:
                yield jdata

    def cleanup_attrs(self, jinfo: dict) -> dict:
        """Return copy of jinfo with ordered required attributes

        Attributes with None value that are not required are not copied
        """
        result = {}
        for a in self.attrs_per_content_item:
            a_key = a["key"]
            if a.get("required"):
                result[a_key] = jinfo.get(a_key)
            elif jinfo.get(a_key) is not None:
                result[a_key] = jinfo[a_key]
        return result

    def get_best_lid(self, jinfo: dict) -> dict:
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
        """Return dictionary with weighted votes per language.

        This method calculates the weighted votes for each language based on the predictions
        from various language identification systems (LIDs). It applies filters for admissible
        languages, minimal probability thresholds, and boosts votes based on predefined
        confidence levels.

        :param dict content_item: A dictionary representing a single content item with LID predictions.
        :return: A Counter object containing the weighted votes for each language.
        :rtype: Optional[Counter]
        """

        # Check if alphabetical_ratio is below the threshold
        if (
            content_item.get("alphabetical_ratio", 1.0)
            < self.alphabetical_ratio_threshold
        ):
            return Counter({self.collection_stats["dominant_language"]: 1})

        # Initialize a dictionary to store votes for each language
        votes = defaultdict(list)

        # Iterate over each LID system to collect votes
        for lid in self.lids:

            # Check if the LID system has predictions for the content item
            if (
                lid in content_item
                and content_item[lid] is not None
                and len(content_item[lid]) > 0
            ):
                lang, prob = content_item[lid][0]["lang"], content_item[lid][0]["prob"]

                # Filter predictions based on admissible languages
                if (
                    self.admissible_languages is None
                    or lang in self.admissible_languages
                ):

                    # Filter predictions based on minimal probability threshold
                    if prob >= self.minimal_lid_probability:
                        lang_support = (
                            self.collection_stats["lg_support"][lid].get(lang) or 0.0
                        )

                        # Calculate the vote score based on confidence levels
                        if lang_support:
                            vote_score = prob * lang_support

                            # Apply special weight for impresso_ft predicting Luxembourgish
                            if lid == "impresso_ft" and lang == "lb":
                                vote_score *= self.weight_lb_impresso_ft

                            # Append the vote score to the list for the language
                            votes[lang].append((lid, vote_score))

        # Aggregate the vote scores for each language
        decision = Counter()

        for lang in votes:
            decision[lang] = sum(vote_score for (_, vote_score) in votes[lang])

        return decision

    def update_impresso_lid_results(self) -> None:
        """Update self.results with all language classification decisions"""

        for c in self.next_content_item():
            log.info(f"Processing {c['id']}")
            self.results.append(self.decide_lg(c))

    def decide_lg(self, content_item: dict) -> dict:
        """Return a dict with decision information for a content item"""

        decided_content_item = {}

        # copy relevant attributes from stage 1 for each content item
        for d in self.attrs_per_content_item:
            if d.get("source") == "language_identifier":
                decided_content_item[d["key"]] = copy.copy(content_item.get(d["key"]))

        decided_content_item["collection"] = decided_content_item["id"][
            0 : len(decided_content_item["id"]) - 19
        ]
        decided_content_item["year"] = decided_content_item["id"][-18:-14]
        decided_content_item.update(
            {
                "impresso_language_identifier_version": {
                    "version": self.git_describe or __version__,
                    "ts": (
                        datetime.datetime.now(datetime.timezone.utc).isoformat(
                            sep="T", timespec="seconds"
                        )
                    ),
                }
            }
        )

        if decided_content_item["tp"] == "img":
            return self.cleanup_attrs(decided_content_item)

        trust_orig_lg = False
        if overall_orig_lg_support := self.collection_stats.get(
            "overall_orig_lg_support"
        ):
            trust_orig_lg = overall_orig_lg_support > self.threshold_confidence_orig_lg

        dominant_lg = self.collection_stats["dominant_language"]

        # rule 1: ignore original language information when not trustworthy
        if not trust_orig_lg or not content_item.get("orig_lg"):
            content_item["orig_lg"] = None
            self.lids.discard("orig_lg")
        else:
            # set confidence value of original language information as probability
            # the original probability was always 1 before
            orig_lg_support = self.collection_stats["lg_support"]["orig_lg"].get(
                content_item["orig_lg"], 0.00001
            )
            # use the original language information only
            content_item["orig_lg"] = [
                {"lang": content_item["orig_lg"], "prob": orig_lg_support}
            ]

        # rule 2
        all_lid_preds = self.get_best_lid(content_item)
        all_lid_languages = set(all_lid_preds[lid]["lang"] for lid in all_lid_preds)

        # rule 2a: follow unequivocal predictions
        if len(all_lid_languages) == 1:
            decided_content_item["lg"] = min(all_lid_languages)
            decided_content_item["lg_decision"] = "all"
            return self.cleanup_attrs(decided_content_item)

        all_but_impresso_ft_lid_languages = set(
            all_lid_preds[lid]["lang"] for lid in all_lid_preds if lid != "impresso_ft"
        )

        # rule 2b: off-the-shelf LID agree on language other than DE or FR
        if len(all_but_impresso_ft_lid_languages) == 1:
            other_lg = min(
                all_but_impresso_ft_lid_languages
            )  # min is just used to select the only element
            if other_lg not in {"de", "fr", "en", "it"} and (
                other_lg in self.collection_stats["lid_distributions"]["ensemble"]
                and content_item["len"] * content_item["alphabetical_ratio"]
                >= self.minimal_text_length
            ):
                decided_content_item["lg"] = other_lg
                decided_content_item["lg_decision"] = "all-but-impresso_ft"
                return self.cleanup_attrs(decided_content_item)

        # rule 2c: set dominant language of collection for very short articles
        if decided_content_item["len"] < self.minimal_text_length:
            decided_content_item["lg"] = dominant_lg
            decided_content_item["lg_decision"] = "dominant-by-len"
            return self.cleanup_attrs(decided_content_item)

        votes = self.get_votes(content_item)

        # keep the votes in for now
        decided_content_item["votes"] = [
            {"lang": k, "vote": round(v, 3)} for k, v in votes.most_common()
        ]
        if len(votes) < 1 or votes.most_common(n=1)[0][1] < self.minimal_voting_score:
            decided_content_item["lg"] = dominant_lg
            decided_content_item["lg_decision"] = "dominant-by-lowvote"
            return self.cleanup_attrs(decided_content_item)

        # rule 3: get decision by ensemble voting for less obvious cases
        decided_content_item["lg"] = votes.most_common(n=1)[0][0]
        decided_content_item["lg_decision"] = "voting"
        return self.cleanup_attrs(decided_content_item)

    def update_stats(self) -> None:
        """Update per-collection statistics for diagnostics"""

        for r in self.results:
            for p in self.stats_keys:
                self.stats[p][r.get(p)] += 1
            self.stats["N"][f'{self.collection_stats["collection"]}-{r["year"]}'] += 1


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
        help=(
            "set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG"
            " (default %(default)s)"
        ),
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
        help="ignore original language when below this threshold (default %(default)s)",
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
        help=(
            "special voting weight for impresso_ft predicting Luxembourgish (default"
            " %(default)s)"
        ),
    )
    parser.add_argument(
        "--minimal-lid-probability",
        metavar="P",
        default=0.5,
        type=float,
        help=(
            "minimal probability for a LID decision to be considered a vote (default"
            " %(default)s)"
        ),
    )
    parser.add_argument(
        "--minimal-voting-score",
        metavar="W",
        default=0.5,
        type=float,
        help=(
            "minimal vote score for voting decision to be accepted (default"
            " %(default)s)"
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "validate final lang identification JSON against schema (default"
            " %(default)s)"
        ),
    )
    parser.add_argument(
        "--diagnostics-json",
        type=str,
        help="filename for statistical diagnostics information in JSON format",
    )
    parser.add_argument(
        "-m",
        "--minimal-text-length",
        default=20,
        type=int,
        help=(
            "minimal text length of content items to apply automatic language"
            " identification (default %(default)s)"
        ),
    )
    parser.add_argument(
        "--lids",
        nargs="+",
        default=[],
        metavar="LID",
        help=(
            "names of all LID systems (e.g. langdetect, langid) to use. Do not add"
            " orig_lg here!"
        ),
    )
    parser.add_argument(
        "--admissible-languages",
        nargs="+",
        default=None,
        metavar="L",
        help=(
            "Names of languages considered in the ensemble decisions. "
            "If None, no restrictions are applied (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--git-describe",
        type=str,
        default="",
        help="git describe output for ingesting version into JSON as version string",
    )
    parser.add_argument(
        "--alphabetical-ratio-threshold",
        default=0.5,
        type=float,
        help=(
            "threshold for alphabetical ratio below which dominant language is selected"
            " (default %(default)s)"
        ),
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
        format="%(asctime)-15s %(filename)s:%(lineno)d %(levelname)s: %(message)s",
    )
    log.info(f"{arguments}")
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
        "diagnostics_json",
        "git_describe",
        "validate",
        "alphabetical_ratio_threshold",
    }
    # launching application ...
    ImpressoLanguageIdentifier(
        **{k: v for k, v in vars(arguments).items() if k in language_identifier_args}
    ).run()
