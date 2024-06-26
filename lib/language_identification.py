#!/usr/bin/env python3

"""
Compute language identification classes and their probabilities with different LID
systems
"""

__version__ = "2024.04.12"

import datetime
import json
import logging
import re
import sys
from collections import Counter
from typing import Dict, List, Optional, Iterable, Set, Union, Tuple

import fasttext
import langdetect
from langdetect.lang_detect_exception import LangDetectException
from langid import langid
import smart_open

log = logging.getLogger(__name__)


def alphabetical_ratio(text: str) -> Optional[float]:
    """Return the percentage of alphabetic characters of a text

    All digits, punctuation symbols, layout characters, are removed

    :param str text: Any text.
    :return: Ratio of alphabetic characters wrt to total length of text.
    :rtype: float

    """

    len_text = len(text)
    if len_text == 0:
        return None
    filtered = re.sub(r"[\W_\d]+", "", text)

    return len(filtered) / len_text


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
    for i in range(n):
        langdetect.DetectorFactory.seed += i
        result = langdetect.detect_langs(text)
        results.append(result)
        if result[0].prob > threshold and result[0].lang in default_languages:
            break

    return average_distribution(results, round_ndigits)


def fasttext_lid(
    text: str, ft_model, round_ndigits: int = 9
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

    labels, probs = ft_model.predict(text, k=3, threshold=0.05)
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

    :param str infile: Path to input file in impresso bz2 rebuilt format.

    :param str outfile: JSON file with language predictions per content item.

    :param str impresso_ft: Path to binary fasttext LID impresso model.

    :param str wp_ft: Path to binary fasttext LID Wikipedia model.

    :param int minimal_text_length: threshold for text length in characters to apply
        automatic language identification.

    :param Set[str] lids: Set of LID systems predict to language/probability pairs.
        Therefore, orig_lg is not seen as LID system as it "predicts" only a single
        language if any.

    :attr type results: Description of parameter `results`

    :param int round_ndigits: Number of decimal places in the output

    :param str git_describe: Output of git describe to use as version if not empty
        string

    :attr list results: Collection of content items with the language prediction of
        various systems.

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
    ):

        self.infile: str = infile
        self.outfile: str = outfile
        self.impresso_ft: str = impresso_ft
        self.wp_ft: str = wp_ft
        self.minimal_text_length: int = minimal_text_length

        self.lids: Set[str] = set(lids)
        log.info(
            f"Predicting with the following off-the-shelve LID systems: {', '.join(lids)}."
        )
        self.round_ndigits = round_ndigits
        self.git_describe = git_describe
        self.results = []

    def run(self):
        """Run the language identification process."""
        log.info(
            "Language identification started with config: "
            f"{json.dumps(vars(self), default=lambda x: list(x) if isinstance(x, set) else x)}"
        )
        self.language_identification()
        self.write_output()
        log.info("Language identification finished.")

    def language_identification(self) -> None:
        """Run multiple language identifications with the models provided and update
        results
        """

        # initialize with langid lid classifier
        langid_lid = langid.LanguageIdentifier.from_modelstring(
            langid.model, norm_probs=True
        )
        # we no longer restrict it to certain languages
        # langid_lid.set_languages(['de', 'fr', 'en', 'lb'])

        # load provided FastText models
        impresso_ft_model = wp_ft_model = None

        if self.impresso_ft is not None:
            impresso_ft_model = fasttext.load_model(self.impresso_ft)
        if self.wp_ft is not None:
            wp_ft_model = fasttext.load_model(self.wp_ft)

        # iterate over content items and apply all LID models
        for j in self.next_contentitem():
            log.info(f"WORKING ON {j['id']}")
            jinfo = {}

            try:
                # initialize information
                jinfo.update(
                    {
                        "tp": j["tp"],
                        "id": j["id"],
                        "len": len(j.get("ft", "")),
                        "orig_lg": j.get("lg"),
                        "language_identifier_version": {
                            "version": self.git_describe or __version__,
                            "ts": datetime.datetime.now(
                                datetime.timezone.utc
                            ).isoformat(sep="T", timespec="seconds"),
                        },
                    }
                )

                # perform lid if text of content item is available and has a minimal length
                if (
                    "ft" in j
                    and isinstance(j["ft"], str)
                    and len(j["ft"].strip()) >= self.minimal_text_length
                ):
                    jinfo["alphabetical_ratio"] = round(
                        alphabetical_ratio(j["ft"]), self.round_ndigits
                    )

                    # predict with langdetect
                    if "langdetect" in self.lids:
                        try:
                            langdetect_result = avg_langdetect_lid(
                                j["ft"], 3, round_ndigits=self.round_ndigits
                            )
                        except LangDetectException:
                            log.error(
                                f"LANGDETECT-ERROR-WITH {jinfo} {j['ft']}  {sys.exc_info()[0]}"
                            )
                            langdetect_result = None
                        jinfo["langdetect"] = langdetect_result

                    # predict with langid
                    if "langid" in self.lids:
                        try:
                            lang_orig, lang_prob_orig = langid_lid.classify(j["ft"])
                            jinfo["langid"] = [
                                {
                                    "lang": lang_orig,
                                    "prob": round(lang_prob_orig, self.round_ndigits),
                                }
                            ]
                        except:
                            log.error(f"LANGID-ERROR-WITH {sys.exc_info()[0]}")
                            jinfo["langid"] = None

                    # fasttext with our own de/fr/lb model
                    if "impresso_ft" in self.lids and impresso_ft_model is not None:
                        try:
                            jinfo["impresso_ft"] = fasttext_lid(
                                j["ft"],
                                impresso_ft_model,
                                round_ndigits=self.round_ndigits,
                            )
                        except:
                            jinfo["impresso_ft"] = None
                            log.error(f"IMPRESSO-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["impresso_ft"] = None

                    # fasttext with public wikipedia model
                    if "wp_ft" in self.lids and wp_ft_model is not None:
                        try:
                            jinfo["wp_ft"] = fasttext_lid(
                                j["ft"], wp_ft_model, round_ndigits=self.round_ndigits
                            )
                        except:
                            jinfo["wp_ft"] = None
                            log.error(f"WP-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["wp_ft"] = None

                self.results.append(jinfo)
            except:
                log.error(f"PROBLEM WITH {sys.exc_info()} {jinfo} {j}")
                exit(1)

    def write_output(self) -> None:
        """
        Write results to jsonline output file.
        """

        with smart_open.open(self.outfile, mode="w", encoding="utf-8") as f_out:
            for r in self.results:
                print(
                    json.dumps(r, ensure_ascii=False, separators=(",", ":")), file=f_out
                )

    def next_contentitem(self) -> Iterable[dict]:
        """
        Yield each contentitem.
        """

        with smart_open.open(self.infile, encoding="utf-8") as reader:
            for line in reader:
                if line.strip():
                    yield json.loads(line)


if __name__ == "__main__":
    import argparse

    DESCRIPTION = (
        "Compute language identification classes and their probabilities "
        "with different LID systems."
    )

    EPILOG = (
        "All tools use two-letter ISO 639-1 codes, except wp_ft which "
        "recognizes additional languages identifiable only by 3 letter codes."
    )
    parser = argparse.ArgumentParser(description=DESCRIPTION, epilog=EPILOG)
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
        help="set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)",
    )
    parser.add_argument(
        "-i",
        "--infile",
        default="/dev/stdin",
        help="path to input file in impresso bz2 rebuilt format (default %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        default="/dev/stdout",
        help="path to output file for impresso lid json format (default %(default)s)",
    )
    parser.add_argument(
        "-m",
        "--minimal-text-length",
        default=20,
        type=int,
        help="minimal text length of content items to apply automatic landuage identification (default %(default)s)",
    )
    parser.add_argument(
        "--lids",
        nargs="+",
        default=[],
        metavar="LID",
        help="names of all LID systems (e.g. langdetect, langid) to use. Do not add orig_lg here!",
    )
    parser.add_argument(
        "--round-ndigits",
        default=9,
        type=int,
        help="round floats in the output to n digits (default %(default)s)",
    )
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
    parser.add_argument(
        "--git-describe",
        type=str,
        default="",
        help="output of git describe command for ingesting git version into JSON as version string",
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
        "impresso_ft",
        "wp_ft",
        "minimal_text_length",
        "round_ndigits",
        "lids",
        "git_describe",
    }

    LanguageIdentifier(
        **{k: v for k, v in vars(arguments).items() if k in language_identifier_args}
    ).run()
