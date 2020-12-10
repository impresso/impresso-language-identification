# Information on impresso language identification (LID)

Identifying the correct language in the multilingual Impresso newspaper collections is challenging. 

Regular LID models are trained on contemporary digital-born texts. OCRized historical newspapers, however, often contain texts with different spelling rules and noisy text. Specifically, texts in Gothic fonts that were wrongly OCRized using Antiqua font settings produce results which can be irritating for the existing models. Moreover, the identification is of particular difficulty when dealing with mixed content of Luxemburgish newspapers where a single article may have several languages. As each content item only features a single language in our classification schema, the identification results in unsolvable cases. Other difficulties originate from radio programs, lengthy records of sports events with many names, which often also confuse standard language identifier.



The digitized newspapers in our collection differ concerning the available metadata on the language of their content items:

* missing information (newspapers without any language information)
* partial information (e.g. no information for ads)
* potentially wrong information



As a result, neither the available metadata nor the individual predictions of a classifier are sufficient to predict the correct language. Therefore, we follow a threefold approach:

1. predict the language of an article using various LID classifiers (stage 1a)
2. aggregate the predictions and make ensemble decision and assess overall confidence of a classifier (stage 1b)
3. predict the final language of an article following a rule-based approach and ensemble voting (stage 2)



For our model `impresso_ft`, we selected and trained specifically on items where the original language was different from the predicted languages, and on multilingual newspapers from Luxembourg (roughly 2000 content items).

Following these steps, you can produce the language identification JSON files underlying the Impresso interface and the downstream processing.

## Prerequisites

This repository uses `pipenv`.

```bash
$ pipenv install  
$ pipenv shell
```

For processing, you have to provide a symbolic link called `rebuilt-data` inside the repository that resolves into the impresso rebuilt data set. Alternatively, you can set the environment variable before running the build commands from the next section.

```bash
export IMPRESSO_REBUILT_DATA_DIR=/PATH/TO/DIRECTORY
```


## Stage 1a: Automatic Language Identification

We first apply several off-the-shelve LID classifiers and our model to the
texts. The corresponding build command is:

```bash
make impresso-lid-stage1a-target
```

This step produces a JSON file per year per collection. As this takes a lot of time, you may want to parallelize the process using multiple machines that work on the same shared files. To avoid redundant operations and the overwriting of files, the Makefile uses a file lock mechanism.

Properties of standard LID tools used in impresso:

  - `langid` LID (recognizes many language, incl. `lb`):
    [https://github.com/saffsd/langid.py]()
  - `langdetect` LID (recognizes many languages, except `lb`):
    [https://github.com/Mimino666/langdetect]()
  - `wp_ft` wikipedia model delivered by fasttext (recognizes many languages,
      incl. `lb`): [https://fasttext.cc/docs/en/language-identification.html]()
  - `impresso_ft` impresso model based on fasttext (recognizes exactly `fr/de/lb`)


## Stage 1b: Computing collection statistics

Given the incomplete and sometimes unreliable metadata regarding the content items' language, we aggregate statistics per collection to assess the confidence in the classifiers. The global statistics allow us to take a more informed decision in the next stage of processing.

In order to assess the dominant language of a newspaper, we compute the statistics per collection according to the following rules:

  - Content items with less than 200 characters are ignored.
  - Content items with an alphabetical ratio < 0.5 are ignored.
  - Every language identification prediction has one vote.
  - If external metadata is available (called `orig_lg` henceforth), it also counts as
    a LID prediction.
  - If the `impresso_ft` or the `orig_lg` vote has support from at least another LID
    model, their votes are boosted by 1.5.
  - The language with the most votes wins and is counted. In case of a tie, we
    don't count for a specific language.

Whenever the ensemble decision matches the original language information from the data providers, this counts as positive support. Whenever the original language information differs from the ensemble decision (excluding any cases where no decision could be reached), this counts as negative support. The proportion of positive support assesses the confidence into the original language information as well as the various LID classifiers. If this threshold is below 75% we ignore the information when determining the final decision per content item in stage 2.

To perform this stage, run the following command:

```bash
make impresso-lid-stage1-target
```

This command can only be run after stage 1a has been completely built. It cannot be run at the same time via different machines. 

## Stage 2: Determining the language per content item

Given the output from various LID systems and the original language information, we finally decide the language of an article according to the following rules:

 - If the overall support for the original language is below 75%, we ignore it completely. Otherwise, the original language is treated the same way as any other LID system.

 - If all LID systems agree unequivocally, we choose this language. In practice, this rule only applies to the languages `de` and `fr` due to the limitations of the `impresso_ft` system. Decision code: `all`.

 - If all LID systems except `impresso_ft` agree on a language other than `de` or `fr`, accept this other language. This rule typically applies for `it`, `la`, or other rare languages.  `lb` is exempt because not all LID systems can recognize `lb`. Decision code: `other`.

 - If the text is shorter than 50 characters, we choose the dominant language of the newspaper. Decision code: `dominant-by-len`.

 - Only if no decision could be made, an ensemble voting is performed. We apply a similar voting technique as in the global statistics step of stage 1b in which the votes are weighed based on their confidence. As the `impresso_ft` is the only reliable system for predicting Luxembourgish, it has the power to overrule other predictions with an additional vote weighting factor of `3` when predicting `lb`.
   
    - If the sum of all votes is below the threshold of `0.5`, we simply choose the dominant language of the newspaper. Decision code: `dominant-by-lowvote`.
    - Otherwise, the language is set according to the evidence based on weighted votes. Decision code: `voting`.

To perform this stage, run the following command:

```bash
make impresso-lid-stage2-target
```

The process of stage 2 is fast and cannot be run on different machines.

## Parallelization
To run the full LID process on a single machine with N cores, run:

```bash
make impresso-lid -j N
```
