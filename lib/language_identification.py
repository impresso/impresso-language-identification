#!/usr/bin/python3
# -*- coding: utf-8 -*-

import logging
import re
import sys
from collections import Counter

import fasttext
import jsonlines
import langdetect
from langdetect.lang_detect_exception import LangDetectException
from langid.langid import LanguageIdentifier, model
from smart_open import open

log = logging.getLogger(__name__)


def average_distribution(listoflist):
    """
    Return dictionary of averaged language:probability key-value pairs

    """
    total = len(listoflist)
    counter = Counter()
    for l in listoflist:
        for r in l:
            counter[r.lang] += r.prob
    for k in counter:
        counter[k] = counter[k] / total
    result = [{"lang": k, "prob": round(counter[k], 2)} for k in counter]
    log.debug(f"DEBUG-LANGDETECT-DIVERSITY {len(listoflist)} {listoflist}")
    return result


def alphabetical_ratio(text):
    """Return the percentage of alphabetic characters

    All digits, punctuation symbols, layout characters, are removed
    """
    len_text = len(text)
    if len_text == 0:
        return None
    filtered = re.sub(r'[\W_\d]+', '', text)
    # log.debug(f"FILTERED-TEXT {filtered}")
    return len(filtered) / len_text


class MainApplication(object):

    def __init__(self, args):
        self.args = args
        self.detectors = ["langdetect", "langid"]  # default LID detectors
        self.jsonlog = {}
        self.results = []  # list of json
        self.year_stats = Counter()
        self.issue_stats = Counter()

    def run(self):
        self.language_identification()
        self.output()

    def language_identification(self):
        """
        Run language identification with available models and update results


        """

        # langid lid classifier
        langid_lid = LanguageIdentifier.from_modelstring(model, norm_probs=True)

        # we no longer restrict it to certain languages
        # langid_lid.set_languages(['de', 'fr', 'en', 'lb'])

        # langdetect lid classifier using n samples from a text

        def avg_langdetect_lid(text, n, threshold=0.95):
            """
            Compute averaged lid from at most n draws.

            For efficiency, drawing stops if the top-most language has a higher probability than threshold
            """
            results = []
            for i in range(n):
                result = langdetect.detect_langs(text)
                results.append(result)
                if result[0].prob > threshold:
                    break
            return average_distribution(results)

        # fasttext lid
        impresso_ft_model = wp_ft_model = None
        if self.args.impresso_ft is not None:
            impresso_ft_model = fasttext.load_model(self.args.impresso_ft)
        if self.args.wp_ft is not None:
            wp_ft_model = fasttext.load_model(self.args.wp_ft)

        def fasttext_lid(text, themodel):
            """
            Return results of a fasttext model

            The only normalization is mapping digits to 0. The internal function predict of fasttext returns a pair
            of tuples

            In [16]: m.predict(''' l'eût cru, le rêve de M. Mitterand, c'est d'e''',k=3)
            Out[16]: (('__label__fr', '__label__lb', '__label__de'),
                     array([9.99996185e-01, 2.38023513e-05, 1.00000034e-05]))
            """
            if themodel is None:
                return None
            # ignore digits
            text = re.sub(r'\d+', '', text)
            labels, probs = themodel.predict(text, k=3, threshold=0.005)
            result = [{"lang": l.replace('__label__', ''), "prob": float(min(1, round(probs[i], 2)))}
                      for (i, l) in
                      enumerate(labels)]
            return result

        # apply all models
        for j in self.next_contentitem():
            log.info(f"WORKING ON {j['id']}")
            jinfo = {}
            try:
                # initialize information
                jinfo.update(
                    {
                        "tp": j["tp"],
                        "cid": j["id"],
                        "len": len(j["ft"]) if "ft" in j and type(j["ft"]) == str else 0,
                        "orig_lg": j["lg"] if "lg" in j else None
                    }
                )

                if "ft" in j and type(j["ft"]) == str and len(j["ft"].strip()) >= self.args.minimal_text_length:
                    jinfo["alphabetical_ratio"] = round(alphabetical_ratio(j["ft"]), 2)

                    # langdetect
                    try:
                        langdetect_result = avg_langdetect_lid(j["ft"], 3)
                    except LangDetectException:
                        log.error(f"LANGDETECT-ERROR-WITH {jinfo} {j['ft']}  {sys.exc_info()[0]}")
                        langdetect_result = None
                    jinfo["langdetect"] = langdetect_result

                    # langid
                    try:
                        lang_orig, lang_prob_orig = langid_lid.classify(j["ft"])
                        jinfo["langid"] = [{"lang": lang_orig, "prob": round(lang_prob_orig, 2)}]
                    except:
                        log.error(f"LANGID-ERROR-WITH {sys.exc_info()[0]}")
                        jinfo["langid"] = None
                        print(f'PROBLEM WITH {jinfo} {j["ft"]}', file=sys.stderr)

                    # fasttext with our own de/fr/lb model
                    if impresso_ft_model is not None:
                        try:
                            jinfo["impresso_ft"] = fasttext_lid(j["ft"], impresso_ft_model)
                        except:
                            jinfo["impresso_ft"] = None
                            log.error(f"IMPRESSO-FT-ERROR-WITH {sys.exc_info()[0]}")
                    else:
                        jinfo["impresso_ft"] = None
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
        with open(self.args.output_file, mode="w", encoding="utf-8") as of:
            writer = jsonlines.Writer(of)
            writer.write_all(self.results)

    def next_contentitem(self):
        """"
        Yield each contentitem
        """
        print(self.args.input_file, file=sys.stderr)
        with open(self.args.input_file, encoding="utf-8") as reader:
            json_reader = jsonlines.Reader(reader)
            for jdata in json_reader:
                yield jdata


if __name__ == '__main__':
    import argparse

    description = "Compute language identification classes and their probability with different lid tools. Per " \
                  "default we use langdetect, langid. Per option two additional fasttext models  can be loaded "
    epilog = ""
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

    MainApplication(args).run()
