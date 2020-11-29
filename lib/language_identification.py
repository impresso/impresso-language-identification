#!/usr/bin/python3
# -*- coding: utf-8 -*-

import logging
import re
import sys
from collections import Counter
from typing import Dict, List, Optional, Iterable

import fasttext
import jsonlines
import langdetect
from langdetect.lang_detect_exception import LangDetectException
from langid.langid import LanguageIdentifier, model
from smart_open import open

log = logging.getLogger(__name__)

__VERSION__ = "2020.11.29"

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
    filtered = re.sub(r'[\W_\d]+', '', text)

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
    result = [{"lang": lang, "prob": round(counter[lang], 2)} for lang in counter]

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
    text = re.sub(r'\d+', '', text)

    labels, probs = ft_model.predict(text, k=3, threshold=0.005)
    result = [{"lang": lang.replace('__label__', ''), "prob": float(min(1, round(probs[i], 2)))}
              for (i, lang) in
              enumerate(labels)]

    return result


class LanguageInfer(object):

    def __init__(self, args: Dict) -> None:
        self.args = args
        self.detectors = ["langdetect", "langid"]  # default LID detectors
        self.jsonlog = {}
        self.results = []  # list of json
        self.year_stats = Counter()
        self.issue_stats = Counter()

    def run(self):
        self.language_identification()
        self.output()

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

        if self.args.impresso_ft is not None:
            impresso_ft_model = fasttext.load_model(self.args.impresso_ft)
        if self.args.wp_ft is not None:
            wp_ft_model = fasttext.load_model(self.args.wp_ft)

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
                        "version": __VERSION__
                    }
                )

                # perform lid if text of content item is available and has a minimal length
                if "ft" in j and isinstance(j["ft"], str) and len(j["ft"].strip()) >= self.args.minimal_text_length:
                    jinfo["alphabetical_ratio"] = round(alphabetical_ratio(j["ft"]), 2)

                    # predict with langdetect
                    try:
                        langdetect_result = avg_langdetect_lid(j["ft"], 3)
                    except LangDetectException:
                        log.error(f"LANGDETECT-ERROR-WITH {jinfo} {j['ft']}  {sys.exc_info()[0]}")
                        langdetect_result = None
                    jinfo["langdetect"] = langdetect_result

                    # predict with langid
                    try:
                        lang_orig, lang_prob_orig = langid_lid.classify(j["ft"])
                        jinfo["langid"] = [{"lang": lang_orig, "prob": round(lang_prob_orig, 2)}]
                    except:
                        log.error(f"LANGID-ERROR-WITH {sys.exc_info()[0]}")
                        jinfo["langid"] = None

                    # fasttext with our own de/fr/lb model
                    if impresso_ft_model is not None:
                        try:
                            jinfo["impresso_ft"] = fasttext_lid(j["ft"], impresso_ft_model)
                        except:
                            jinfo["impresso_ft"] = None
                            log.error(f"IMPRESSO-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["impresso_ft"] = None

                    # fasttext with public wikipedia model
                    if wp_ft_model is not None:
                        try:
                            jinfo["wp_ft"] = fasttext_lid(j["ft"], wp_ft_model)
                        except:
                            jinfo["wp_ft"] = None
                            log.error(f"WP-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["wp_ft"] = None

                self.results.append(jinfo)
            except:
                log.error(f'PROBLEM WITH {sys.exc_info()} {jinfo} {j}')
                exit(1)

    def output(self):
        """
        Output json
        """
        with open(self.args.output_file, mode="w", encoding="utf-8") as f_out:
            writer = jsonlines.Writer(f_out)
            writer.write_all(self.results)

    def next_contentitem(self) -> Iterable[dict]:
        """"
        Yield each contentitem
        """

        with open(self.args.input_file, encoding="utf-8") as reader:
            json_reader = jsonlines.Reader(reader)
            for jdata in json_reader:
                yield jdata


if __name__ == '__main__':
    import argparse

    description = "Compute language identification classes and their probability with different lid tools. Per " \
                  "default we use langdetect, langid. Per option two additional fasttext models  can be loaded "
    epilog = "All tools use two-letter ISO 639-1 codes, except wp_ft which recognizes additional languages identifiable only by 3 letter codes."
    parser = argparse.ArgumentParser(description=description, epilog=epilog)
    parser.add_argument('-l', '--logfile', dest='logfile',
                        help='write log to FILE', metavar='FILE')
    parser.add_argument('-v', '--verbose', dest='verbose', default=2, type=int, metavar="LEVEL",
                        help='set verbosity level: 0=CRITICAL, 1=ERROR, 2=WARNING, 3=INFO 4=DEBUG (default %(default)s)')
    parser.add_argument('-i', '--input-file', default="/dev/stdin",
                        help="path to input file in impresso bz2 rebuilt format")
    parser.add_argument('-o', '--output-file', default="/dev/stdout",
                        help="path to output file for impresso lid json format")
    parser.add_argument('-m', '--minimal-text-length', default=20, type=int,
                        help="minimal text length of content items to apply automatic landuage identification (default %(default)s)")
    parser.add_argument('-j', '--json-log-file', default=None,
                        help="Most important statistics and output collected in a structured JSON file")
    parser.add_argument('--impresso_ft', default=None, help="Binary fasttext LID impresso model labeled impresso_ft "
                                                            "in the output", metavar="FILE")
    parser.add_argument('--wp_ft', default=None, help="Binary fasttext wikipedia LID model labeled wp_ft in the "
                                                      "output ", metavar="FT2")

    args = parser.parse_args()

    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
                  logging.INFO, logging.DEBUG]

    logging.basicConfig(level=log_levels[args.verbose],
                        format='%(asctime)-15s %(levelname)s: %(message)s')

    LanguageInfer(args).run()
