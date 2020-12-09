#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""
Compute language identification classes and their probabilities with different LID systems.
"""

__version__ = "2020.11.30"

import logging
import re
import sys
import datetime
from collections import Counter
from typing import Dict, List, Optional, Iterable

import fasttext
import jsonlines
import langdetect
from langdetect.lang_detect_exception import LangDetectException
from langid.langid import LanguageIdentifier, model
from smart_open import open

log = logging.getLogger(__name__)

langdetect.DetectorFactory.seed = 42


def alphabetical_ratio(text: str) -> Optional[float]:
    """Return the percentage of alphabetic characters of a text

    All digits, punctuation symbols, layout characters, are removed

    :param str text: Any text.
    :return: Ratio of alphabetic characters wrt to total lenght of text.
    :rtype: float

    """

    len_text = len(text)
    if len_text == 0:
        return None
    filtered = re.sub(r"[\W_\d]+", "", text)

    return len(filtered) / len_text


def average_distribution(listoflist: List[List]) -> List[Dict[str, float]]:
    """Return dictionary of averaged probabilities per language.

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

    result = [{"lang": lang, "prob": round(prob, 2)} for lang, prob in counter.most_common()]

    log.debug(f"DEBUG-LANGDETECT-DIVERSITY Length: {len(listoflist)} Predictions: {listoflist}")

    return result


def avg_langdetect_lid(text: str, n: int, threshold: float = 0.95) -> List[Dict[str, float]]:
    """Compute averaged lid score from n samples using Langdetect.

    For efficiency, drawing stops if the top-most language has a higher probability than threshold

    :param str text: Text to classify.
    :param int n: Number of samples.
    :param float threshold: Threshold for early-stopping of sampling.
    :return: Dictionary with the averaged probabilities per language
    :rtype: List[Dict[str, float]]

    """

    results = []
    for i in range(n):
        result = langdetect.detect_langs(text)
        results.append(result)
        if result[0].prob > threshold:
            break

    return average_distribution(results)


def fasttext_lid(text: str, ft_model) -> List[Dict[str, float]]:
    """
    Return results of a fasttext model.

    The only normalization is mapping digits to 0. The internal function predict of fasttext returns a pair
    of tuples

    In [16]: m.predict(''' l'eût cru, le rêve de M. Mitterand, c'est d'e''',k=3)
    Out[16]: (('__label__fr', '__label__lb', '__label__de'),
             array([9.99996185e-01, 2.38023513e-05, 1.00000034e-05]))
    """

    # ignore digits
    text = re.sub(r"\d+", "", text)

    labels, probs = ft_model.predict(text, k=3, threshold=0.005)
    result = [
        {"lang": lang.replace("__label__", ""), "prob": float(min(1, round(probs[i], 2)))}
        for (i, lang) in enumerate(labels)
    ]

    return result


class LanguageInfer:
    """Predict languages for content items.

    :param str infile: Path to input file in impresso bz2 rebuilt format.
    :param str outfile: JSON file with language predictions per content item.
    :param str impresso_ft: Path to binary fasttext LID impresso model.
    :param str wp_ft: Path to binary fasttext LID Wikipedia model.
    :param int minimal_text_length: threshold for text length in characters to apply automatic language identification.
    :param Set[str] lids: Set of LID systems predict to language/probability pairs.
        Therefore, orig_lg is not seen as LID system as it "predicts" only a single language if any.    :attr type results: Description of parameter `results`.

    :attr list results: Collection of content items with the language prediction of various systems.

    """

    def __init__(
        self,
        infile: str,
        outfile: str,
        impresso_ft: str,
        wp_ft: str,
        minimal_text_length: int,
        lids: list,
    ):

        self.infile: str = infile
        self.outfile: str = outfile
        self.impresso_ft: str = impresso_ft
        self.wp_ft: str = wp_ft
        self.minimal_text_length: int = minimal_text_length

        self.lids: list = lids
        log.info(f"Predicting with the following off-the-shelve LID systems: {', '.join(lids)}.")

        self.results = []

    def run(self):
        log.info(f"Language identification started.")
        self.language_identification()
        self.write_output()
        log.info(f"Language identification finished.")

    def language_identification(self) -> None:
        """Run multiple language identifications with the models provided and update results

        :return: None.
        :rtype: None

        """

        # initialize with langid lid classifier
        langid_lid = LanguageIdentifier.from_modelstring(model, norm_probs=True)
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
                        "len": len(j["ft"]) if "ft" in j and isinstance(j["ft"], str) else 0,
                        "orig_lg": j["lg"] if "lg" in j else None,
                        "version": __version__,
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(
                            sep="T", timespec="seconds"
                        ),
                    }
                )

                # perform lid if text of content item is available and has a minimal length
                if (
                    "ft" in j
                    and isinstance(j["ft"], str)
                    and len(j["ft"].strip()) >= self.minimal_text_length
                ):
                    jinfo["alphabetical_ratio"] = round(alphabetical_ratio(j["ft"]), 2)

                    # predict with langdetect
                    if "langdetect" in self.lids:
                        try:
                            langdetect_result = avg_langdetect_lid(j["ft"], 3)
                        except LangDetectException:
                            log.error(
                                f"LANGDETECT-ERROR-WITH {jinfo} {j['ft']}  {sys.exc_info()[0]}"
                            )
                            langdetect_result = None
                        jinfo["langdetect"] = langdetect_result

                    # predict with langid
                    if "landid" in self.lids:
                        try:
                            lang_orig, lang_prob_orig = langid_lid.classify(j["ft"])
                            jinfo["langid"] = [
                                {"lang": lang_orig, "prob": round(lang_prob_orig, 2)}
                            ]
                        except:
                            log.error(f"LANGID-ERROR-WITH {sys.exc_info()[0]}")
                            jinfo["langid"] = None

                    # fasttext with our own de/fr/lb model
                    if "impresso_ft" in self.lids and impresso_ft_model is not None:
                        try:
                            jinfo["impresso_ft"] = fasttext_lid(j["ft"], impresso_ft_model)
                        except:
                            jinfo["impresso_ft"] = None
                            log.error(f"IMPRESSO-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["impresso_ft"] = None

                    # fasttext with public wikipedia model
                    if "wp_ft" in self.lids and wp_ft_model is not None:
                        try:
                            jinfo["wp_ft"] = fasttext_lid(j["ft"], wp_ft_model)
                        except:
                            jinfo["wp_ft"] = None
                            log.error(f"WP-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["wp_ft"] = None

                self.results.append(jinfo)
            except:
                log.error(f"PROBLEM WITH {sys.exc_info()} {jinfo} {j}")
                exit(1)

    def write_output(self):
        """
        Write results to json.
        """
        with open(self.outfile, mode="w", encoding="utf-8") as f_out:
            writer = jsonlines.Writer(f_out)
            writer.write_all(self.results)

    def next_contentitem(self) -> Iterable[dict]:
        """ "
        Yield each contentitem.
        """

        with open(self.infile, encoding="utf-8") as reader:
            json_reader = jsonlines.Reader(reader)
            for jdata in json_reader:
                yield jdata


if __name__ == "__main__":
    import argparse

    DESCRIPTION = "Compute language identification classes and their probabilities with different LID systems."
    EPILOG = "All tools use two-letter ISO 639-1 codes, except wp_ft which recognizes additional languages identifiable only by 3 letter codes."
    parser = argparse.ArgumentParser(description=DESCRIPTION, epilog=EPILOG)
    parser.add_argument("-l", "--logfile", dest="logfile", help="write log to FILE", metavar="FILE")
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
        "-i",
        "--infile",
        default="/dev/stdin",
        help="path to input file in impresso bz2 rebuilt format",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        default="/dev/stdout",
        help="path to output file for impresso lid json format",
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
        "--impresso-ft",
        default=None,
        help="binary fasttext LID impresso model labeled impresso_ft in the output",
        metavar="FILE",
    )
    parser.add_argument(
        "--wp-ft",
        default=None,
        help="binary fasttext wikipedia LID model labeled wp_ft in the output ",
        metavar="FT2",
    )

    arguments = parser.parse_args()

    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]

    logging.basicConfig(
        level=log_levels[arguments.verbose], format="%(asctime)-15s %(levelname)s: %(message)s"
    )

    LanguageInfer(**arguments).run()
