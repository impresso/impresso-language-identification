# Information on impresso language identification (LID)

Identifying the correct language in the multilingual Impresso newspaper
collections is challenging.

Regular LID models are trained on contemporary digital-born texts. OCRized
historical newspapers, however, often contain texts with different spelling
rules and noisy text. Specifically, texts in Gothic fonts that were wrongly
OCRized using Antiqua font settings produce results which can be irritating for
the existing models. Moreover, the identification is of particular difficulty
when dealing with mixed content of Luxemburgish newspapers where a single
article may have several languages. As each content item only features a single
language in our classification schema, the identification results in unsolvable
cases. Other difficulties originate from radio programs, lengthy records of
sports events with many names, which often also confuse standard language
identifier.


The digitized newspapers in our collection differ concerning the available
metadata on the language of their content items:

* missing information (newspapers without any language information)
* partial information (e.g. no information for ads)
* potentially wrong information


As a result, neither the available metadata nor the individual predictions of a
classifier are sufficient to predict the correct language. Therefore, we follow
a three-step approach:

1. predict the language of an article using various probabilistic language
   identification classifiers (stage 1a)
2. aggregate the predictions, compute an ensemble decision for longer articles
   and assess the confidence of a classifier by comparing against the ensemble
   decision (stage 1b)
3. predict the final language of an article following a rule-based approach and
   ensemble voting (stage 2)


For our model `impresso_ft`, we selected and trained specifically on items where
the original language was different from the predicted languages, and on
multilingual newspapers from Luxembourg (roughly 2000 content items).

Following these steps, you can produce the language identification JSON files
underlying the Impresso interface and the downstream processing.

## Prerequisites
The build process has been tested on modern Linux and macOS systems and requires
Python 3.11. Under Debian, make sure to have the following packages installed:

```sh
$ # install python3.11 according to your OS
$ sudo apt install git git-lfs make moreutils  # needed for building
$ sudo apt rclone  # needed for uploading to s3
$ sudo apt jq  # needed for computing statistics
```

This repository uses `pipenv`.

```sh
$ git clone https://github.com/impresso/impresso-language-identification.git
$ cd impresso-language-identification
$ python3.11 -mpip install pipenv
$ python3.11 -mpipenv install
$ python3.11 -mpipenv shell
```

For processing, you have to provide a symbolic link called `rebuilt-data` inside
the repository that resolves into the impresso rebuilt data set. Alternatively,
you can set the environment variable before running the build commands from the
next section. The folder `test/rebuilt-data` contains some test data to play
with.

```sh
export IMPRESSO_REBUILT_DATA_DIR=/PATH/TO/DIRECTORY
```


## Stage 1a: Automatic Language Identification

We first apply several off-the-shelve LID classifiers and our model to the
texts. The corresponding build command is:

```sh
make impresso-lid-stage1a-target
```

This step produces a JSON file per year per collection. As this takes a lot of
time, you may want to parallelize the process using multiple machines that work
on the same shared files. To avoid redundant operations and overwriting of
files, the Makefile implements a file lock mechanism.

Properties of standard LID tools used in impresso:

  - `langid` LID (recognizes many language, incl. `lb`):
    [https://github.com/saffsd/langid.py]()
  - `langdetect` LID (recognizes many languages, except `lb`):
    [https://github.com/Mimino666/langdetect]()
  - `wp_ft` wikipedia model delivered by fasttext (recognizes many languages,
      incl. `lb`): [https://fasttext.cc/docs/en/language-identification.html]()
  - `impresso_ft` impresso model based on fasttext (recognizes exactly
    `fr/de/lb/en/it`)


## Stage 1b: Aggregating collection statistics on language

Given the incomplete and sometimes unreliable metadata regarding the content
items' language, we aggregate statistics per collection to assess the confidence
in the classifiers. The global statistics allow us to take a more informed
decision in the next stage of processing.

In order to assess the dominant language of a newspaper, we compute the
statistics per collection according to the following rules:

  - Content items with less than 200 non-letter characters are ignored.
  - Content items with an alphabetical ratio < 0.5 are ignored.
  - Every language identification prediction has one vote.
  - If external metadata is available (called `orig_lg` henceforth), it also
    counts as a LID prediction.
  - If the `impresso_ft` or the `orig_lg` vote has support from at least another
    LID model, their votes are boosted by 1.5.
  - The language with the most votes wins and is counted. In case of a tie, we
    don't count for a specific language.

Whenever the ensemble decision matches the original language information from
the data providers, this counts as positive support. Whenever the original
language information differs from the ensemble decision (excluding any cases
where no decision could be reached), this counts as negative support. The
proportion of positive support assesses the confidence into the original
language information as well as the various LID classifiers. If this threshold
is below 75% we ignore the information when determining the final decision per
content item in stage 2.

To perform this stage, run the following command:

```sh
make impresso-lid-stage1-target  
```

This command can only be run after stage 1a has been completely built. It cannot
be run at the same time via different machines.

## Stage 2: Deciding the language per content item

Given the output from various LID systems and the original language information,
we finally decide the language of an article according to the following rules:

 - If the overall support for the original language is below 75%, we ignore it
   completely. Otherwise, the original language is treated the same way as any
   other LID system.

 - If all LID systems agree unequivocally, we choose this language. In practice,
   this rule only applies to the languages `de`, `fr`, `en` and `it` due to the
   limitations of the `impresso_ft` system. Decision code: `all`.

 - If all LID systems except `impresso_ft` agree on a language other than `de`,
   `fr`, `en` or `it`, and if the language has been selected by the ensemble in
   stage 1b at least once, and if there are at least as many letter characters
   as the minimal text length specifies, accept this other language. This rule
   typically applies for `la`, or other rare languages.  `lb` is exempt because
   not all LID systems can recognize `lb`. Decision code: `all-but-impresso_ft`.

 - If the text is shorter than 50 characters, we choose the dominant language of
   the newspaper. Decision code: `dominant-by-len`.

 - Only if no decision could be made, an ensemble voting is performed. We apply
   a similar voting technique as in the global statistics step of stage 1b in
   which the votes are weighed based on their confidence. As the `impresso_ft`
   is the only reliable system for predicting Luxembourgish, it has the power to
   overrule other predictions with an additional vote weighting factor of `6`
   when predicting `lb`.
   
    - If the sum of all votes is below the threshold of `0.5`, we simply choose
      the dominant language of the newspaper. Decision code:
      `dominant-by-lowvote`.
    - Otherwise, the language is set according to the evidence based on weighted
      votes. Decision code: `voting`.

To perform this stage, run the following command:

```sh
make impresso-lid-stage2-target
```

The process of stage 1b and 2 is fast and cannot be run on different machines.

## Preparing the data release

Preparing the LID data release involves the following steps:
  - Validating the jsonl files from stage 2 according to impresso's [language
    identification JSON
    schema](https://github.com/impresso/impresso-schemas/blob/master/json/language_identification/language_identification.schema.json).
- Copying over the per-collection aggregation statistics from stage 1b.
- Preparing statistical diagnostics files for the whole impresso collection set

```sh
make impresso-lid-release-target
```

After preparing the data one can upload to a configured s3 bucket
```sh
make impresso-lid-upload-release-to-s3 
```

## Creating LID statistics
During stage 2 for each per-year collection output, diagnostics files in JSON
format are produced that aggregate the information from the individual content
item files.

```sh
make impresso-lid-statistics
```

## Parallelization

To run the full LID process on a single machine with N cores, run:

```sh
make impresso-lid -j N
```
Because the step 1a is taking a lot of time for millions of content items, it is
recommended to build in parallel on several machines that can access the same
storage.
