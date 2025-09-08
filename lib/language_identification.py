#!/usr/bin/env python3

"""
Language identification module for newspaper content items.

This module provides a flexible framework for applying multiple language identification
(LID) systems to content items. It supports various LID systems including:
- langdetect: Statistical language detection
- langid: Language identification using n-gram features
- FastText models: Including custom impresso and Wikipedia models
- impresso_langident_pipeline: Advanced language identification using the impresso pipeline

The module uses a dynamic registry pattern to easily add new LID systems and handles
text validation, model initialization, and result aggregation in a modular way.

Key features:
- Configurable text length and alphabetical ratio thresholds
- Support for variable number of LID models
- Robust error handling for individual models
- S3 and local file support for input/output
- Comprehensive logging with structured output

Example usage:
    processor = LanguageIdentifier(
        infile="input.jsonl",
        outfile="output.jsonl",
        lids=["langdetect", "langid"],
        minimal_text_length=20,
        alphabetical_ratio_threshold=0.0  # Default: no alphabetical ratio filtering
    )
    processor.run()
"""

__version__ = "2025.06.21"

import json
import logging
import re
import sys
import time
from collections import Counter
from typing import Dict, List, Optional, Iterable, Set, Union, Tuple


import fasttext
import langdetect
from langdetect.lang_detect_exception import LangDetectException
from langid import langid
import smart_open

try:
    from impresso_pipelines.langident import LangIdentPipeline

    IMPRESSO_LANGIDENT_PIPELINE_AVAILABLE = True
except ImportError:
    IMPRESSO_LANGIDENT_PIPELINE_AVAILABLE = False

try:
    from lingua import LanguageDetectorBuilder, Language, IsoCode639_1

    LINGUA_AVAILABLE = True
except ImportError:
    LINGUA_AVAILABLE = False


from impresso_cookbook import get_s3_client, get_timestamp

log = logging.getLogger(__name__)

# Log warning if impresso_pipelines is not available
if not IMPRESSO_LANGIDENT_PIPELINE_AVAILABLE:
    log.warning(
        "impresso_pipelines package not available - impresso_langident_pipeline will"
        " not be functional"
    )
    log.warning(
        "Please install it with 'pip install impresso_pipelines' to use this feature."
    )

# Log warning if lingua is not available
if not LINGUA_AVAILABLE:
    log.warning("lingua package not available - lingua will not be functional")
    log.warning(
        "Please install it with 'pip install lingua-language-detector' to use this"
        " feature."
    )


def alphabetical_ratio(text: str) -> float:
    """Return the percentage of alphabetic characters of a text."""
    if not text:
        return 0.0
    filtered_length = len(re.sub(r"[\W_\d]+", "", text))
    return filtered_length / len(text) if filtered_length else 0.0


def average_distribution(
    listoflist: List[List], round_ndigits: int = 9
) -> List[Dict[str, Union[str, float]]]:
    """Return dictionary of averaged probabilities per language.

    :param int round_ndigits: Number of decimal places for probabilities
    :param List[List] listoflist: Results of multiple language identification.
    :return: Dictionary with the averaged probabilities per language
    :rtype: List[Dict[str, float]]

    """

    total = len(listoflist)
    counter = Counter()
    for row in listoflist:
        for r in row:
            counter[r.lang] += r.prob
    for lang in counter:
        counter[lang] = counter[lang] / total

    result = [
        {"lang": lang, "prob": round(prob, round_ndigits)}
        for lang, prob in counter.most_common()
    ]

    log.debug(
        "DEBUG-LANGDETECT-DIVERSITY Length: %s Predictions: %s",
        len(listoflist),
        listoflist,
    )

    return result


def avg_langdetect_lid(
    text: str,
    n: int,
    threshold: float = 0.95,
    seed: int = 42,
    default_languages: Tuple[str] = ("de", "fr"),
    round_ndigits: int = 9,
) -> List[Dict[str, Union[str, float]]]:
    """Compute averaged lid score from n samples using Langdetect.

    For efficiency, drawing stops if the top-most language has a higher probability than
    threshold

    :param int round_ndigits: Number of decimal places for probabilities.
    :param str text: Text to classify.
    :param int n: Number of samples.
    :param int seed: Initial random seed for langdetect
    :param Set[str] default_languages: Set of language where early stopping is allowed
        for highly probably languages
    :param float threshold: Threshold for early-stopping of sampling.
    :return: Dictionary with the averaged probabilities per language
    :rtype: List[Dict[str, float]]

    """
    langdetect.DetectorFactory.seed = seed

    results = []
    lower = text.lower()
    text = lower  # add lower case text to increase detection probability
    for i in range(n):
        langdetect.DetectorFactory.seed += i
        result = langdetect.detect_langs(text)
        results.append(result)
        if result[0].prob > threshold and result[0].lang in default_languages:
            break

    return average_distribution(results, round_ndigits)


def fasttext_lid(
    text: str, ft_model, round_ndigits: int = 3
) -> List[Dict[str, Union[str, float]]]:
    """
    Return results of a fasttext model.

    The only normalization is mapping digits to 0. The internal function predict of
    fasttext returns a pair of tuples

    In [16]: m.predict(''' l'eût cru, le rêve de M. Mitterand, c'est d'e''',k=3)
    Out[16]: (('__label__fr', '__label__lb', '__label__de'),
             array([9.99996185e-01, 2.38023513e-05, 1.00000034e-05]))
    """

    # ignore digits
    text = re.sub(r"\d+", "", text)

    labels, probs = ft_model.predict(text, k=5, threshold=0.05)
    result = [
        {
            "lang": lang.replace("__label__", ""),
            "prob": float(min(1, round(probs[i], round_ndigits))),
        }
        for (i, lang) in enumerate(labels)
    ]

    return result


class LanguageIdentifier(object):
    """Predict languages for content items.

    This class applies multiple language identification systems to newspaper content
    items using a flexible, registry-based approach. It handles text validation,
    model initialization, and result aggregation.

    :param str infile: Path to input file in impresso bz2 rebuilt format.
    :param str outfile: JSON file with language predictions per content item.
    :param str impresso_ft: Path to binary fasttext LID impresso model.
    :param str wp_ft: Path to binary fasttext LID Wikipedia model.
    :param int minimal_text_length: Threshold for text length in characters to apply
        automatic language identification.
    :param list lids: List of LID systems to use (e.g., ['langdetect', 'langid']).
        Available systems: langdetect, langid, impresso_ft, wp_ft, impresso_langident_pipeline, lingua.
    :param int round_ndigits: Number of decimal places in the output.
    :param str git_describe: Output of git describe command for version tracking.
    :param float alphabetical_ratio_threshold: Minimum ratio of alphabetic characters
        required for language identification.

    :attr list results: Collection of content items with language predictions.
    """

    def __init__(
        self,
        infile: str,
        outfile: str,
        impresso_ft: str,
        wp_ft: str,
        minimal_text_length: int,
        lids: list,
        round_ndigits: int,
        git_describe: str,
        alphabetical_ratio_threshold: float,
    ):

        self.infile: str = infile
        self.outfile: str = outfile
        self.impresso_ft: str = impresso_ft
        self.wp_ft: str = wp_ft
        self.minimal_text_length: int = minimal_text_length

        self.lids: Set[str] = set(lids)
        log.info(
            "Predicting with the following off-the-shelve LID systems: %s.",
            ", ".join(lids),
        )
        self.round_ndigits = round_ndigits
        self.git_describe = git_describe
        self.s3_client = get_s3_client()
        self.results = []
        self.alphabetical_ratio_threshold = alphabetical_ratio_threshold
        self.start_time = None
        self.ts = get_timestamp()
        self.stats = {
            "processed_items": 0,
            "skipped_no_text": 0,
            "skipped_short_text": 0,
            "skipped_low_alpha": 0,
            "language_identified": 0,
            "language_disagreements": 0,
        }

    def run(self):
        """Run the language identification process."""
        self.start_time = time.time()

        log.info(
            "Starting language identification process for input file: %s", self.infile
        )
        log.info("Output will be written to: %s", self.outfile)
        log.info("Using LID systems: %s", ", ".join(self.lids))

        self.language_identification()
        self.write_output()

        # Log statistics
        self._log_statistics()

        # Log compute time
        total_time = time.time() - self.start_time
        log.info(
            "Language identification finished for %s in %.2f seconds.",
            self.infile,
            total_time,
        )

    def _initialize_models(self):
        """Initialize language identification models based on requested LID systems."""
        models = {}

        log.info("Initializing models for input file: %s", self.infile)

        # Define model initializers
        model_initializers = {
            "langid": lambda: langid.LanguageIdentifier.from_modelstring(
                langid.model, norm_probs=True
            ),
            "impresso_ft": lambda: (
                fasttext.load_model(self.impresso_ft) if self.impresso_ft else None
            ),
            "wp_ft": lambda: fasttext.load_model(self.wp_ft) if self.wp_ft else None,
            "impresso_langident_pipeline": lambda: (
                LangIdentPipeline() if IMPRESSO_LANGIDENT_PIPELINE_AVAILABLE else None
            ),
            "lingua": lambda: (
                LanguageDetectorBuilder.from_all_languages().build()
                if LINGUA_AVAILABLE
                else None
            ),
        }

        # Initialize only requested models
        for lid_system in self.lids:
            if lid_system in model_initializers:
                try:
                    model = model_initializers[lid_system]()
                    if model is not None:
                        models[lid_system] = model
                        log.info(
                            "Successfully loaded %s model for %s",
                            lid_system,
                            self.infile,
                        )
                    else:
                        log.warning(
                            "Model path not provided for %s when processing %s",
                            lid_system,
                            self.infile,
                        )
                        if (
                            lid_system == "impresso_langident_pipeline"
                            and not IMPRESSO_LANGIDENT_PIPELINE_AVAILABLE
                        ):
                            log.warning(
                                "impresso_pipelines package not available for %s",
                                self.infile,
                            )
                        if lid_system == "lingua" and not LINGUA_AVAILABLE:
                            log.warning(
                                "lingua package not available for %s", self.infile
                            )
                except Exception as e:
                    log.error(
                        "Failed to load %s model for %s: %s", lid_system, self.infile, e
                    )
            elif (
                lid_system != "langdetect"
            ):  # langdetect doesn't need model initialization
                log.warning(
                    "Unknown LID system %s when processing %s", lid_system, self.infile
                )

        return models

    def _apply_langdetect(
        self, text: str
    ) -> Optional[List[Dict[str, Union[str, float]]]]:
        """Apply langdetect language identification."""
        try:
            return avg_langdetect_lid(text, 3, round_ndigits=self.round_ndigits)
        except LangDetectException:
            log.error(
                "LANGDETECT-ERROR for %s with text: %s %s",
                self.infile,
                text,
                sys.exc_info()[0],
            )
            return None

    def _apply_langid(
        self, text: str, model
    ) -> Optional[List[Dict[str, Union[str, float]]]]:
        """Apply langid language identification."""
        try:
            lang_orig, lang_prob_orig = model.classify(text.lower())
            return [
                {
                    "lang": lang_orig,
                    "prob": round(lang_prob_orig, self.round_ndigits),
                }
            ]
        except Exception:
            log.error("LANGID-ERROR for %s: %s", self.infile, sys.exc_info()[0])
            return None

    def _apply_fasttext(
        self, text: str, model, model_name: str
    ) -> Optional[List[Dict[str, Union[str, float]]]]:
        """Apply FastText language identification."""
        try:
            return fasttext_lid(text, model, round_ndigits=self.round_ndigits)
        except Exception:
            log.error(
                "%s-ERROR for %s: %s | Input: %s",
                model_name.upper(),
                self.infile,
                sys.exc_info()[0],
                text,
                exc_info=True,
            )
            return None

    def _apply_impresso_langident_pipeline(
        self, text: str, model
    ) -> Optional[List[Dict[str, Union[str, float]]]]:
        """Apply impresso_pipelines language identification."""
        try:
            predictions = model(text, diagnostics=True)["diagnostics"]["languages"]
            result = [
                {"lang": r["language"], "prob": prob}
                for r in predictions
                if (prob := r["score"]) > 0.05
            ]
            # probabilites are already rounded in the pipeline
            return result
        except Exception:
            log.error(
                "IMPRESSO-LANGIDENT-PIPELINE-ERROR for %s: %s",
                self.infile,
                sys.exc_info()[0],
            )
            return None

    def _apply_lingua(
        self, text: str, model
    ) -> Optional[List[Dict[str, Union[str, float]]]]:
        """Apply lingua language identification."""
        try:
            confidence_values = model.compute_language_confidence_values(text.lower())
            result = [
                {
                    "lang": confidence.language.iso_code_639_1.name.lower(),
                    "prob": round(confidence.value, self.round_ndigits),
                }
                for confidence in confidence_values
                if confidence.value > 0.05  # Filter out very low confidence predictions
            ]
            return result
        except Exception:
            log.error("LINGUA-ERROR for %s: %s", self.infile, sys.exc_info()[0])
            return None

    def _perform_language_identification(
        self, text: str, models: dict, jinfo: dict
    ) -> None:
        """Perform language identification with all configured models."""

        # Define model handlers
        model_handlers = {
            "langdetect": lambda: self._apply_langdetect(text),
            "langid": lambda: (
                self._apply_langid(text, models["langid"])
                if "langid" in models
                else None
            ),
            "impresso_ft": lambda: (
                self._apply_fasttext(text, models["impresso_ft"], "impresso_ft")
                if "impresso_ft" in models
                else None
            ),
            "wp_ft": lambda: (
                self._apply_fasttext(text, models["wp_ft"], "wp_ft")
                if "wp_ft" in models
                else None
            ),
            "impresso_langident_pipeline": lambda: (
                self._apply_impresso_langident_pipeline(
                    text, models["impresso_langident_pipeline"]
                )
                if "impresso_langident_pipeline" in models
                else None
            ),
            "lingua": lambda: (
                self._apply_lingua(text, models["lingua"])
                if "lingua" in models
                else None
            ),
        }

        # Apply each requested LID system
        for lid_system in self.lids:
            if lid_system in model_handlers:
                result = model_handlers[lid_system]()
                jinfo[lid_system] = result
                if result is None:
                    log.debug(
                        "No result from %s language identifier for %s",
                        lid_system,
                        self.infile,
                    )
            else:
                log.warning(
                    "No handler defined for LID system %s when processing %s",
                    lid_system,
                    self.infile,
                )
                jinfo[lid_system] = None

    def _create_base_info(self, content_item: dict) -> dict:
        """Create base information dictionary for a content item."""
        return {
            "tp": content_item["tp"],
            "id": content_item["id"],
            "len": len(content_item.get("ft", "")),
            "orig_lg": content_item.get("lg"),
            "ts": self.ts,
            "langident_stage1_version": self.git_describe or __version__,
        }

    def _is_text_valid_for_lid(self, content_item: dict) -> tuple[bool, str, float]:
        """
        Check if text is valid for language identification.

        Returns:
            Tuple of (is_valid, text, alphabetical_ratio_value)
        """
        if "ft" not in content_item or not isinstance(content_item["ft"], str):
            return False, "", 0.0

        text = content_item["ft"].strip()
        if len(text) < self.minimal_text_length:
            return False, text, 0.0

        alpha_ratio = round(alphabetical_ratio(text), 2)
        if alpha_ratio < self.alphabetical_ratio_threshold:
            return False, text, alpha_ratio

        return True, text, alpha_ratio

    def _check_language_disagreements(self, jinfo: dict) -> None:
        """Check for disagreements between language identifiers and log them."""
        # Extract best predictions from each model that returned results
        best_predictions = {}

        for lid_system in self.lids:
            if lid_system in jinfo and jinfo[lid_system] is not None:
                results = jinfo[lid_system]
                if isinstance(results, list) and len(results) > 0:
                    # Get the top prediction (highest probability)
                    best_lang = results[0]["lang"]
                    best_predictions[lid_system] = best_lang

        # Check if we have at least 2 predictions to compare
        if len(best_predictions) < 2:
            return

        # Check if all predictions agree
        unique_predictions = set(best_predictions.values())
        if len(unique_predictions) > 1:
            # Log disagreement with document ID and all predictions
            predictions_str = ", ".join(
                [f"{lid}:{lang}" for lid, lang in best_predictions.items()]
            )
            log.info("LANGUAGE-DISAGREEMENT %s: %s", jinfo["id"], predictions_str)
            self.stats["language_disagreements"] += 1

            # Create confusion counter key from sorted unique predicted languages
            sorted_languages = sorted(unique_predictions)
            confusion_key = f"LID_DISAGREEMENT_{'_'.join(sorted_languages)}"
            if confusion_key not in self.stats:
                self.stats[confusion_key] = 0
            self.stats[confusion_key] += 1

    def _log_statistics(self):
        """Log processing statistics."""
        total = self.stats["processed_items"]
        if total > 0:
            log.info("STATS-PROCESSED-ITEMS\t%d (100.0%%)", total)
            log.info(
                "STATS-SKIPPED-NO-TEXT\t%d (%.1f%%)",
                self.stats["skipped_no_text"],
                (self.stats["skipped_no_text"] / total) * 100,
            )
            log.info(
                "STATS-SKIPPED-SHORT-TEXT\t%d (%.1f%%)",
                self.stats["skipped_short_text"],
                (self.stats["skipped_short_text"] / total) * 100,
            )
            log.info(
                "STATS-SKIPPED-LOW-ALPHA\t%d (%.1f%%)",
                self.stats["skipped_low_alpha"],
                (self.stats["skipped_low_alpha"] / total) * 100,
            )
            log.info(
                "STATS-LANGUAGE-IDENTIFIED\t%d (%.1f%%)",
                self.stats["language_identified"],
                (self.stats["language_identified"] / total) * 100,
            )
            log.info(
                "STATS-LANGUAGE-DISAGREEMENTS\t%d (%.1f%%)",
                self.stats["language_disagreements"],
                (self.stats["language_disagreements"] / total) * 100,
            )

            # Log confusion counters
            confusion_stats = {
                k: v for k, v in self.stats.items() if k.startswith("LID_DISAGREEMENT_")
            }
            for confusion_key in sorted(confusion_stats.keys()):
                count = confusion_stats[confusion_key]
                log.info(
                    "STATS-%s\t%d (%.1f%%)", confusion_key, count, (count / total) * 100
                )
        else:
            log.info("STATS-PROCESSED-ITEMS\t%d", total)
            log.info("STATS-SKIPPED-NO-TEXT\t%d", self.stats["skipped_no_text"])
            log.info("STATS-SKIPPED-SHORT-TEXT\t%d", self.stats["skipped_short_text"])
            log.info("STATS-SKIPPED-LOW-ALPHA\t%d", self.stats["skipped_low_alpha"])
            log.info("STATS-LANGUAGE-IDENTIFIED\t%d", self.stats["language_identified"])
            log.info(
                "STATS-LANGUAGE-DISAGREEMENTS\t%d", self.stats["language_disagreements"]
            )

            # Log confusion counters
            confusion_stats = {
                k: v for k, v in self.stats.items() if k.startswith("LID_DISAGREEMENT_")
            }
            for confusion_key in sorted(confusion_stats.keys()):
                count = confusion_stats[confusion_key]
                log.info("STATS-%s\t%d", confusion_key, count)

    def language_identification(self) -> None:
        """Run multiple language identifications with the models provided and update results."""
        models = self._initialize_models()

        for content_item in self.next_contentitem():
            log.debug("WORKING ON %s", content_item["id"])

            try:
                self.stats["processed_items"] += 1
                jinfo = self._create_base_info(content_item)
                is_valid, text, alpha_ratio = self._is_text_valid_for_lid(content_item)

                if not is_valid:
                    if "ft" not in content_item or not isinstance(
                        content_item["ft"], str
                    ):
                        log.info(
                            "Skipping %s from %s - no valid text field",
                            content_item["id"],
                            self.infile,
                        )
                        self.stats["skipped_no_text"] += 1
                    elif len(text) < self.minimal_text_length:
                        log.info(
                            "Skipping %s from %s - insufficient text length: %d < %d",
                            content_item["id"],
                            self.infile,
                            len(text),
                            self.minimal_text_length,
                        )
                        self.stats["skipped_short_text"] += 1
                    else:
                        log.info(
                            "Skipping %s from %s - low alphabetical ratio: %.2f < %.2f",
                            content_item["id"],
                            self.infile,
                            alpha_ratio,
                            self.alphabetical_ratio_threshold,
                        )
                        self.stats["skipped_low_alpha"] += 1

                    self.results.append(jinfo)
                    continue

                # Text is valid for language identification
                jinfo["alphabetical_ratio"] = round(alpha_ratio, self.round_ndigits)
                self._perform_language_identification(text, models, jinfo)

                # Check for disagreements between language identifiers
                self._check_language_disagreements(jinfo)

                self.stats["language_identified"] += 1
                self.results.append(jinfo)

            except Exception:
                log.error(
                    "PROBLEM processing %s from %s: %s %s %s",
                    content_item.get("id", "unknown"),
                    self.infile,
                    sys.exc_info(),
                    jinfo,
                    content_item,
                )
                exit(1)

    def write_output(self) -> None:
        """Write results to JSON Lines output file."""
        log.info(
            "Writing %d results from %s to %s",
            len(self.results),
            self.infile,
            self.outfile,
        )
        with smart_open.open(self.outfile, mode="w", encoding="utf-8") as f_out:
            for r in self.results:
                f_out.write(
                    json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
        log.info("Successfully wrote output for %s to %s", self.infile, self.outfile)

    def next_contentitem(self) -> Iterable[dict]:
        """Yield each content item from the input file."""
        if self.infile.startswith("s3://"):
            transport_params = {"client": self.s3_client}
        else:
            transport_params = {}
        with smart_open.open(
            self.infile, transport_params=transport_params, encoding="utf-8"
        ) as reader:
            for line in reader:
                if line.strip():
                    yield json.loads(line)


def setup_logging(log_level: int, log_file: Optional[str]) -> None:
    """Configure logging."""

    class SmartFileHandler(logging.FileHandler):
        def _open(self):
            return smart_open.open(self.baseFilename, self.mode, encoding="utf-8")

    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(SmartFileHandler(log_file, mode="w"))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)-15s %(filename)s:%(lineno)d %(levelname)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def main():
    import argparse

    DESCRIPTION = (
        "Identify languages and their probabilities with different LID systems."
    )

    EPILOG = (
        "All tools use two-letter ISO 639-1 codes, except wp_ft which "
        "recognizes additional languages identifiable only by 3 letter codes."
    )
    parser = argparse.ArgumentParser(description=DESCRIPTION, epilog=EPILOG)

    # Input and Output Files
    parser.add_argument(
        "-i",
        "--infile",
        default="/dev/stdin",
        help="path to input file in impresso rebuilt format (default %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        default="/dev/stdout",
        help="path to output file for impresso lid json format (default %(default)s)",
    )

    # Language Identification Systems
    parser.add_argument(
        "--lids",
        nargs="+",
        default=[
            "langdetect",
            "langid",
            "impresso_ft",
            "wp_ft",
            "impresso_langident_pipeline",
            "lingua",
        ],
        choices=[
            "langdetect",
            "langid",
            "impresso_ft",
            "wp_ft",
            "impresso_langident_pipeline",
            "lingua",
        ],
        metavar="LID",
        help=(
            "names of all LID systems (e.g. langdetect, langid) to use. Do not add"
            " orig_lg here! %(default)s)"
        ),
    )

    # Models
    parser.add_argument(
        "--impresso-ft",
        default=None,
        help="binary fasttext LID impresso model labeled impresso_ft in the output)",
        metavar="FILE",
    )
    parser.add_argument(
        "--wp-ft",
        default=None,
        help="binary fasttext wikipedia LID model labeled wp_ft in the output ",
        metavar="FT2",
    )

    # Text Length and Precision
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
        "--round-ndigits",
        default=3,
        type=int,
        help="round floats in the output to n digits (default %(default)s)",
    )

    # Logging and Verbosity
    parser.add_argument(
        "-l", "--logfile", dest="logfile", help="write log to FILE", metavar="FILE"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        default=3,
        type=int,
        metavar="LEVEL",
        help=(
            "set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG"
            " (default %(default)s)"
        ),
    )

    # Version Information
    parser.add_argument(
        "--git-describe",
        type=str,
        default="",
        help=(
            "output of git describe command for ingesting git version into JSON as"
            " version string"
        ),
    )

    # Add alphabetical_ratio_threshold to command-line arguments
    parser.add_argument(
        "--alphabetical-ratio-threshold",
        default=0.0,
        type=float,
        help=(
            "Threshold for alphabetical ratio below which language identification is"
            " skipped (default %(default)s)"
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

    setup_logging(log_levels[arguments.verbose], arguments.logfile)

    log.info("%s", arguments)

    # Directly call LanguageIdentifier with relevant arguments
    processor = LanguageIdentifier(
        infile=arguments.infile,
        outfile=arguments.outfile,
        impresso_ft=arguments.impresso_ft,
        wp_ft=arguments.wp_ft,
        minimal_text_length=arguments.minimal_text_length,
        lids=arguments.lids,
        round_ndigits=arguments.round_ndigits,
        git_describe=arguments.git_describe,
        alphabetical_ratio_threshold=arguments.alphabetical_ratio_threshold,
    )
    processor.run()


if __name__ == "__main__":
    main()
