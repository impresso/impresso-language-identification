#!/usr/bin/env python3

"""
Determine the language of an Impresso content item using ensemble decision making.

This module implements an ensemble language identification system that combines
predictions from multiple language identification systems to make final language
decisions for Impresso newspaper content items.

The script takes two intermediate JSON files as input:
1. A JSONLines file with language predictions per content item from various LID systems
2. A JSON file with global statistics and collection-level information

The ensemble decision process includes multiple rules:
- Unequivocal predictions (all systems agree)
- Agreement among off-the-shelf LID systems
- Length-based fallback to dominant collection language
- Weighted voting with confidence scores
- Special handling for original language metadata

Example:
    $ python impresso_ensemble_lid.py \\
        -i predictions.jsonl \\
        -o final_decisions.jsonl \\
        -C newspaper_stats.json \\
        --lids langdetect langid impresso_ft \\
        --validate

"""

__version__ = "2025.06.24"

import copy
import datetime
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from typing import DefaultDict, Iterable, List, Optional, Set

import jsonschema
import smart_open

from impresso_cookbook import get_s3_client, get_timestamp, read_json, setup_logging

log = logging.getLogger(__name__)


class ImpressoLanguageIdentifier(object):
    """Identify language for each content item using ensemble decision

    :param str infile: JSON file with language predictions per content item.
    :param str outfile: Path to folder where processed JSON files should be saved.
    :param str newspaper_stats_filename: JSON file with aggregated statistics per
        newspaper. Read in into the attribute newspaper_stats
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
        newspaper_stats_filename: str,
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
        dominant_language_threshold: Optional[float] = None,
        exclude_lb: Optional[Set[str]] = None,
    ):

        self.git_describe: str = git_describe

        self.diagnostics_json: Optional[str] = diagnostics_json

        # Add timing and S3 client support
        self.start_time: Optional[float] = None
        self.s3_client = get_s3_client()
        self.ts = get_timestamp()

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
                {"key": "newspaper", "required": False},
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
            log.error("No LID specified. At least one language identifier needed.")
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
        self.dominant_language_threshold: float = dominant_language_threshold or 0.90
        self.exclude_lb: Set[str] = set(exclude_lb) if exclude_lb else set()

        self.schema: Optional[dict] = None
        self.schema_validator: Optional[jsonschema.validators.Draft6Validator] = None
        self.stats: DefaultDict[str, Counter] = defaultdict(Counter)
        self.stats_keys: List[str] = ["lg", "orig_lg", "tp", "lg_decision"]
        self.newspaper_stats: dict = read_json(newspaper_stats_filename, self.s3_client)
        self.results: List[dict] = []

        self.validate: bool = validate
        if self.validate:
            self.load_schema()

    def run(self):
        """Run the application.

        This method orchestrates the entire language identification process by:
        1. Processing all content items and making language decisions
        2. Writing the results to the output file
        3. Updating and writing diagnostic statistics
        """

        self.start_time = time.time()

        log.info("Starting ensemble language identification")
        log.info("Input file: %s", self.infile)
        log.info("Output file: %s", self.outfile)
        log.info("Using LID systems: %s", ", ".join(self.lids))

        self.update_impresso_lid_results()
        self.write_output()
        self.update_stats()
        self.write_diagnostics()

        # Log compute time
        total_time = time.time() - self.start_time
        log.info(
            "Ensemble language identification finished in %.2f seconds.", total_time
        )

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
        """Write JSONlines output to the specified output file.

        This method writes all processed language identification results to the
        output file in JSONLines format, where each line contains a complete
        content item with its final language decision.
        """

        # Handle S3 transport parameters
        if self.outfile.startswith("s3://"):
            transport_params = {"client": self.s3_client}
        else:
            transport_params = {}

        with smart_open.open(
            self.outfile, mode="w", encoding="utf-8", transport_params=transport_params
        ) as of:
            for result in self.results:
                print(json.dumps(result, ensure_ascii=False), file=of)

    def write_diagnostics(self) -> None:
        """Write JSON diagnostics with per-newspaper stats.

        This method writes diagnostic information including statistics and metadata
        about the language identification process to the specified diagnostics file
        in JSON format.
        """

        if self.diagnostics_json:
            # Handle S3 transport parameters
            if self.diagnostics_json.startswith("s3://"):
                transport_params = {"client": self.s3_client}
            else:
                transport_params = {}

            with smart_open.open(
                self.diagnostics_json,
                mode="w",
                encoding="utf-8",
                transport_params=transport_params,
            ) as of:
                print(json.dumps(self.stats), file=of)

    def next_content_item(self) -> Iterable[dict]:
        """Yield next content item from the input file.

        This generator function reads the input JSONLines file and yields
        each content item as a dictionary for processing.

        :return: Iterator over content item dictionaries.
        :rtype: Iterable[dict]
        """

        # Handle S3 transport parameters
        if self.infile.startswith("s3://"):
            transport_params = {"client": self.s3_client}
        else:
            transport_params = {}

        with smart_open.open(
            self.infile, mode="r", encoding="utf-8", transport_params=transport_params
        ) as reader:
            for line in reader:
                line = line.strip()
                if line:  # Skip empty lines
                    yield json.loads(line)

    def cleanup_attrs(self, jinfo: dict) -> dict:
        """Return copy of jinfo with ordered required attributes.

        Attributes with None value that are not required are not copied over.

        :param dict jinfo: Content item dictionary to clean up.
        :return: Cleaned content item dictionary with ordered attributes.
        :rtype: dict
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
        """Extract the top prediction from each LID system.

        For each language identification system, this method extracts only the
        highest-confidence prediction, discarding any additional predictions.

        :param dict jinfo: Content item dictionary with LID predictions.
        :return: Dictionary mapping LID system names to their top predictions.
        :rtype: dict
        """
        result = {}
        for lid_system in self.lids:
            lid_preds = jinfo.get(lid_system)
            if lid_preds and len(lid_preds) > 0:
                result[lid_system] = lid_preds[0]
        return result

    def get_votes(self, content_item: dict) -> Optional[Counter]:
        """Return dictionary with weighted votes per language.

        This method calculates the weighted votes for each language based on the
        predictions from various language identification systems (LIDs). It applies
        filters for admissible languages, minimal probability thresholds, and boosts
        votes based on predefined confidence levels.

        :param dict content_item: A dictionary representing a single content item
            with LID predictions.
        :return: A Counter object containing the weighted votes for each language.
        :rtype: Optional[Counter]
        """

        # Check if alphabetical_ratio is below the threshold
        if (
            content_item.get("alphabetical_ratio", 1.0)
            < self.alphabetical_ratio_threshold
        ):
            log.debug(
                "Content item %s: Alphabetical ratio %s below threshold %s, using"
                " dominant language",
                content_item["id"],
                content_item.get("alphabetical_ratio", 1.0),
                self.alphabetical_ratio_threshold,
            )
            return Counter({self.newspaper_stats["dominant_language"]: 1})

        # Initialize a dictionary to store votes for each language
        votes = defaultdict(list)
        log.debug("Content item %s: Starting vote calculation", content_item["id"])

        # Iterate over each LID system to collect votes
        for lid in self.lids:

            # Check if the LID system has predictions for the content item
            if (
                lid in content_item
                and content_item[lid] is not None
                and len(content_item[lid]) > 0
            ):
                lang, prob = content_item[lid][0]["lang"], content_item[lid][0]["prob"]
                log.debug(
                    "Content item %s: %s predicts %s with probability %s",
                    content_item["id"],
                    lid,
                    lang,
                    prob,
                )

                # Filter predictions based on admissible languages
                if (
                    self.admissible_languages is None
                    or lang in self.admissible_languages
                ):
                    # Check if this newspaper should exclude lb language
                    newspaper_id = content_item["id"][0 : len(content_item["id"]) - 19]
                    if lang == "lb" and newspaper_id in self.exclude_lb:
                        log.debug(
                            "Content item %s: %s prediction of %s excluded for"
                            " newspaper %s",
                            content_item["id"],
                            lid,
                            lang,
                            newspaper_id,
                        )
                        continue

                    # Filter predictions based on minimal probability threshold
                    if prob >= self.minimal_lid_probability:
                        lang_support = (
                            self.newspaper_stats["lg_support"][lid].get(lang) or 0.0
                        )
                        log.debug(
                            "Content item %s: %s language support for %s: %s",
                            content_item["id"],
                            lid,
                            lang,
                            lang_support,
                        )

                        # Calculate the vote score based on confidence levels
                        if lang_support:
                            vote_score = prob * lang_support

                            # Check if newspaper has strong dominance and this is not the dominant language
                            dominant_lang = self.newspaper_stats["dominant_language"]
                            dominant_lang_ratio = self.newspaper_stats.get(
                                "dominant_language_ratio", 0.0
                            )

                            if (
                                dominant_lang_ratio >= self.dominant_language_threshold
                                and lang != dominant_lang
                            ):
                                # Apply penalty for non-dominant languages in highly dominant newspapers
                                dominance_penalty = 1.0 - (
                                    dominant_lang_ratio
                                    - self.dominant_language_threshold
                                ) / (1.0 - self.dominant_language_threshold)
                                original_score = vote_score
                                vote_score *= dominance_penalty
                                log.debug(
                                    "Content item %s: Applied dominance penalty to %s"
                                    " for %s: %s * %s = %s (dominant lang: %s,"
                                    " ratio: %s)",
                                    content_item["id"],
                                    lid,
                                    lang,
                                    original_score,
                                    dominance_penalty,
                                    vote_score,
                                    dominant_lang,
                                    dominant_lang_ratio,
                                )

                            log.debug(
                                "Content item %s: %s initial vote score for %s: %s "
                                "(prob %s * support %s)",
                                content_item["id"],
                                lid,
                                lang,
                                vote_score,
                                prob,
                                lang_support,
                            )

                            # Apply special weight for impresso_ft predicting Luxembourgish
                            if lid == "impresso_ft" and lang == "lb":
                                original_score = vote_score
                                vote_score *= self.weight_lb_impresso_ft
                                log.debug(
                                    "Content item %s: Applied Luxembourgish boost to"
                                    " %s: %s * %s = %s",
                                    content_item["id"],
                                    lid,
                                    original_score,
                                    self.weight_lb_impresso_ft,
                                    vote_score,
                                )

                            # Append the vote score to the list for the language
                            votes[lang].append((lid, vote_score))
                            log.debug(
                                "Content item %s: Added vote for %s from %s: %s",
                                content_item["id"],
                                lang,
                                lid,
                                vote_score,
                            )
                        else:
                            log.debug(
                                "Content item %s: %s - "
                                "No language support for %s, vote rejected",
                                content_item["id"],
                                lid,
                                lang,
                            )
                    else:
                        log.debug(
                            "Content item %s: %s probability "
                            "%s below threshold %s, "
                            "vote rejected",
                            content_item["id"],
                            lid,
                            prob,
                            self.minimal_lid_probability,
                        )
                else:
                    log.debug(
                        "Content item %s: %s language %s "
                        "not in admissible languages, vote rejected",
                        content_item["id"],
                        lid,
                        lang,
                    )
            else:
                log.debug(
                    "Content item %s: No predictions from %s", content_item["id"], lid
                )

        # Aggregate the vote scores for each language
        decision = Counter()

        for lang in votes:
            total_score = sum(vote_score for (_, vote_score) in votes[lang])
            decision[lang] = total_score
            contributing_lids = [lid for (lid, _) in votes[lang]]
            log.debug(
                "Content item %s: Total vote for %s: %s from systems: %s",
                content_item["id"],
                lang,
                total_score,
                contributing_lids,
            )

        if decision:
            log.debug(
                "Content item %s: Final vote scores: %s",
                content_item["id"],
                dict(decision.most_common()),
            )
        else:
            log.debug("Content item %s: No votes collected", content_item["id"])

        return decision

    def update_impresso_lid_results(self) -> None:
        """Update self.results with all language classification decisions.

        This method processes each content item from the input file and makes
        language identification decisions, storing the results in self.results.
        """

        for c in self.next_content_item():
            log.info("Processing %s", c["id"])
            self.results.append(self.decide_lg(c))

    def decide_lg(self, content_item: dict) -> dict:
        """Return a dict with decision information for a content item.

        This method applies the ensemble decision rules to determine the final
        language for a content item. It handles various scenarios including
        image content, unequivocal predictions, length-based decisions, and
        weighted voting.

        :param dict content_item: Content item with language predictions.
        :return: Content item with final language decision and metadata.
        :rtype: dict
        """

        decided_content_item = {}

        # copy relevant attributes from stage 1 for each content item
        for d in self.attrs_per_content_item:
            if d.get("source") == "language_identifier":
                decided_content_item[d["key"]] = copy.copy(content_item.get(d["key"]))

        decided_content_item["newspaper"] = decided_content_item["id"][
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
        if overall_orig_lg_support := self.newspaper_stats.get(
            "overall_orig_lg_support"
        ):
            trust_orig_lg = overall_orig_lg_support > self.threshold_confidence_orig_lg

        log.debug(
            "Content item %s: Original language trust check - "
            "overall_orig_lg_support: %s, "
            "threshold: %s, "
            "trust_orig_lg: %s",
            content_item["id"],
            overall_orig_lg_support,
            self.threshold_confidence_orig_lg,
            trust_orig_lg,
        )

        dominant_lg = self.newspaper_stats["dominant_language"]
        log.debug(
            "Content item %s: Dominant language: %s", content_item["id"], dominant_lg
        )

        # rule 1: ignore original language information when not trustworthy
        if not trust_orig_lg or not content_item.get("orig_lg"):
            log.debug(
                "Content item %s: Rule 1 - "
                "Ignoring original language (trust_orig_lg: %s, "
                "orig_lg present: %s)",
                content_item["id"],
                trust_orig_lg,
                bool(content_item.get("orig_lg")),
            )
            content_item["orig_lg"] = None
            self.lids.discard("orig_lg")
        else:
            # set confidence value of original language information as probability
            # the original probability was always 1 before
            orig_lg_support = self.newspaper_stats["lg_support"]["orig_lg"].get(
                content_item["orig_lg"], 0.00001
            )
            log.debug(
                "Content item %s: Rule 1 - Using original language %s with support %s",
                content_item["id"],
                content_item["orig_lg"],
                orig_lg_support,
            )
            # use the original language information only
            content_item["orig_lg"] = [
                {"lang": content_item["orig_lg"], "prob": orig_lg_support}
            ]

        # rule 2
        all_lid_preds = self.get_best_lid(content_item)
        all_lid_languages = set(all_lid_preds[lid]["lang"] for lid in all_lid_preds)

        log.debug(
            "Content item %s: All LID predictions: %s",
            content_item["id"],
            all_lid_preds,
        )
        log.debug(
            "Content item %s: All predicted languages: %s",
            content_item["id"],
            all_lid_languages,
        )

        # rule 2a: follow unequivocal predictions
        if len(all_lid_languages) == 1:
            decided_language = min(all_lid_languages)
            log.debug(
                "Content item %s: Rule 2a - All systems agree on language: %s",
                content_item["id"],
                decided_language,
            )
            decided_content_item["lg"] = decided_language
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
            log.debug(
                "Content item %s: Rule 2b - All non-impresso_ft systems agree on: %s",
                content_item["id"],
                other_lg,
            )

            text_length_condition = (
                content_item["len"] * content_item["alphabetical_ratio"]
                >= self.minimal_text_length
            )
            in_ensemble_distribution = (
                other_lg in self.newspaper_stats["lid_distributions"]["ensemble"]
            )
            is_non_major_language = other_lg not in {"de", "fr", "en", "it"}

            log.debug(
                "Content item %s: Rule 2b conditions - non-major"
                " language: %s, in ensemble distribution:"
                " %s, text length sufficient:"
                " %s (len=%s,"
                " alpha_ratio=%s,"
                " threshold=%s)",
                content_item["id"],
                is_non_major_language,
                in_ensemble_distribution,
                text_length_condition,
                content_item["len"],
                content_item["alphabetical_ratio"],
                self.minimal_text_length,
            )

            if (
                is_non_major_language
                and in_ensemble_distribution
                and text_length_condition
            ):
                log.debug(
                    "Content item %s: Rule 2b accepted - language: %s",
                    content_item["id"],
                    other_lg,
                )
                decided_content_item["lg"] = other_lg
                decided_content_item["lg_decision"] = "all-but-impresso_ft"
                return self.cleanup_attrs(decided_content_item)
            else:
                log.debug(
                    "Content item %s: Rule 2b rejected for %s",
                    content_item["id"],
                    other_lg,
                )

        # rule 2c: set dominant language of newspaper for very short articles
        if decided_content_item["len"] < self.minimal_text_length:
            log.debug(
                "Content item %s: Rule 2c - Text too short"
                " (%s < %s), using"
                " dominant language: %s",
                content_item["id"],
                decided_content_item["len"],
                self.minimal_text_length,
                dominant_lg,
            )
            decided_content_item["lg"] = dominant_lg
            decided_content_item["lg_decision"] = "dominant-by-len"
            return self.cleanup_attrs(decided_content_item)

        votes = self.get_votes(content_item)

        # keep the votes in for now
        decided_content_item["votes"] = [
            {"lang": k, "vote": round(v, 3)} for k, v in votes.most_common()
        ]

        log.debug(
            "Content item %s: Vote results: %s",
            content_item["id"],
            [{"lang": k, "vote": round(v, 3)} for k, v in votes.most_common()],
        )

        if len(votes) < 1:
            log.debug(
                "Content item %s: No votes received, using dominant language: %s",
                content_item["id"],
                dominant_lg,
            )
            decided_content_item["lg"] = dominant_lg
            decided_content_item["lg_decision"] = "dominant-by-lowvote"
            return self.cleanup_attrs(decided_content_item)

        best_vote_score = votes.most_common(n=1)[0][1]
        if best_vote_score < self.minimal_voting_score:
            log.debug(
                "Content item %s: Best vote score %s "
                "below threshold %s, "
                "using dominant language: %s",
                content_item["id"],
                best_vote_score,
                self.minimal_voting_score,
                dominant_lg,
            )
            decided_content_item["lg"] = dominant_lg
            decided_content_item["lg_decision"] = "dominant-by-lowvote"
            return self.cleanup_attrs(decided_content_item)

        # rule 3: get decision by ensemble voting for less obvious cases
        winning_language = votes.most_common(n=1)[0][0]
        log.debug(
            "Content item %s: Rule 3 - Voting decision: %s with score %s",
            content_item["id"],
            winning_language,
            best_vote_score,
        )
        decided_content_item["lg"] = winning_language
        decided_content_item["lg_decision"] = "voting"
        return self.cleanup_attrs(decided_content_item)

    def update_stats(self) -> None:
        """Update per-newspaper statistics for diagnostics.

        This method processes the results to compute statistics about language
        identification decisions per newspaper and year, which are used for
        diagnostic purposes and quality assessment.
        """

        for r in self.results:
            for p in self.stats_keys:
                self.stats[p][r.get(p)] += 1
            self.stats["N"][f'{self.newspaper_stats["newspaper"]}-{r["year"]}'] += 1


if __name__ == "__main__":
    import argparse

    DESCRIPTION = (
        "Classify language of impresso content items given all collected evidence"
    )

    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--log-file",
        "--logfile",
        dest="log_file",
        help="Write log to FILE",
        metavar="FILE",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: %(default)s)",
    )
    parser.add_argument(
        "-C",
        "--newspaper-stats-filename",
        type=str,
        required=True,
        help="newspaper statistics JSON file",
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
        required=True,
        help="path to input file from s3 batch, json format",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        required=True,
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
    parser.add_argument(
        "--dominant-language-threshold",
        default=0.90,
        type=float,
        help=(
            "threshold for dominant language ratio above which non-dominant languages "
            "receive penalty in voting (default %(default)s)"
        ),
    )
    parser.add_argument(
        "--exclude-lb",
        nargs="+",
        default=[],
        metavar="NEWSPAPER",
        help=(
            "newspaper acronyms for which Luxembourgish (lb) language predictions "
            "should be excluded (default: %(default)s)"
        ),
    )

    arguments = parser.parse_args()

    setup_logging(arguments.log_level, arguments.log_file)

    # Suppress debug messages from third-party libraries
    # Only show WARNING and above for these noisy libraries
    third_party_loggers = [
        "smart_open",
        "smart_open_lib",
        "urllib3",
        "urllib3.connectionpool",
        "botocore",
        "boto3",
        "s3transfer",
        "requests",
        "connectionpool",
        "hooks",
        "parsers",
    ]

    for logger_name in third_party_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    log.info("%s", arguments)

    # Create ImpressoLanguageIdentifier instance
    ImpressoLanguageIdentifier(
        infile=arguments.infile,
        outfile=arguments.outfile,
        newspaper_stats_filename=arguments.newspaper_stats_filename,
        lids=set(arguments.lids),
        weight_lb_impresso_ft=arguments.weight_lb_impresso_ft,
        minimal_lid_probability=arguments.minimal_lid_probability,
        minimal_text_length=arguments.minimal_text_length,
        threshold_confidence_orig_lg=arguments.threshold_confidence_orig_lg,
        minimal_voting_score=arguments.minimal_voting_score,
        admissible_languages=arguments.admissible_languages,
        diagnostics_json=arguments.diagnostics_json,
        git_describe=arguments.git_describe,
        validate=arguments.validate,
        alphabetical_ratio_threshold=arguments.alphabetical_ratio_threshold,
        dominant_language_threshold=arguments.dominant_language_threshold,
        exclude_lb=set(arguments.exclude_lb),
    ).run()
