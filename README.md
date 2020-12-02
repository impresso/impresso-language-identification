# Information on impresso language identification (LID)

Identifying the correct language in the multilingual impresso newspaper
collections is a challenge.  Normal LID models are trained on contemporary
digital-born texts. OCRized historical newspapers often contain texts with
different spelling rules and/or noisy text. Specifically also texts in Gothic
fonts that were wrongly OCRized using Antiqua font settings produce results
which can be irritating for the existing models. Especially difficult is the
recognition of the mixed content of Luxemburgish newspapers where we sometimes
find several languages in a single article. As we need a single language
category per content item, there remain unsolvable cases. Other difficulties
include radio programs, long result lists from sport events with many names,
which quite often also confuse normal language identifier.

For our own model (`impresso_ft`), we selected and trained specifically on items
where the original language was different from the predicted languages, and on
multilingual material from Luxembourg (roughly 2000 content items).

The digitized newspapers in our collection differ with respect to the available
metadata on the language of their content items:

  - missing information (newspapers without any language information)
  - partial information (e.g. no information for ads)
  - wrong information

With the following steps the language identification JSON files are created,
which are used in the impresso interface and in the preceding processing steps.

## Prerequites

This repository uses pipenv.

    $ pipenv install  
    $ pipenv shell

For processing, you either have to provide a symbolic link called `rebuilt-data`
inside the repository that resolves into the impresso rebuilt data set,
alternatively you can export the environment variable before running the build
commands from the next section.

    export IMPRESSO_REBUILT_DATA_DIR=/PATH/TO/DIRECTORY


## Stage 1a: Automatic Language identification

We first apply several off-the-shelve LID classifiers and our own model to the
texts. The corresponding build command is:
 
    make impresso-lid-stage1a-target

This step produces a JSON file per year per collection. As this takes a lot of time, you can run the command on different machines that work on the same shared files.

Properties of standard LID tools used in impresso:

  - `langid` LID (recognizes many language, incl. `lb`):
    [https://github.com/saffsd/langid.py]()
  - `langdetect` LID (recognizes many languages, except `lb`):
    [https://github.com/Mimino666/langdetect]()
  - `wp_ft` wikipedia model delivered by fasttext (recognizes many languages,
      incl. lb): [https://fasttext.cc/docs/en/language-identification.html]()
  - `impresso_ft` impresso model based on fasttext (recognizes exactly `fr/de/lb`)


## Stage 1b: Computing collection statistics

Given the incomplete and sometimes unreliable metadata regarding the content
 items' language, we aggregate statistics per collection. This allows us a more
 informed decision for the next stage of processing.

 In order to assess the dominant language of a newspaper, we compute statistics
 according to the following rules:
 
  - Content items with less than 200 characters are ignored.
  - Content items with an alphabetical ratio < 0.5 are ignored.
  - Every language identification prediction has one vote.
  - If external metadata exists (called `orig_lg` henceforth), it counts also as
    a LID prediction.
  - If the `impresso_ft` or the `orig_lg` vote has support from another LID
    model, their votes are boosted by 1.5.
  - The language with the most votes wins and is counted. In case of a tie, we
    don't count for a specific language.
 
Whenever the ensemble decision matches the original language information from
the data providers, this counts a positive support. Whenever the original
language information differs from the ensemble decision (excluding any cases
where no decision could be reached), this counts as a negative support. The
proportion of positive support assesses the confidence into the original
language information. If this threshold is below 75% we ignore the information
when determining the final decision per content item in the last step.

    make impresso-lid-stage1-target

This command only be run after stage 1a has been completely built. Cannot be run at the same time via different machines. 

## Stage 2: Determining the language per content item

Given the output from different LID systems and the original language
information we finally decide according to the following rules:

 - If the overall support for the original language is below 75%, we ignore it
  completely. Otherwise the original language is treated the same way as any other LID system.
 - If all LID systems agree, we choose this language (this is naturally restricted to the languages `de` and `fr`
  due to the limitations of `impresso_ft` system). Decision code: `all`
 - If all LID systems except `impresso_ft` agree on a language other than `de` or
   `fr` (typically `it`, `la`, or other rare languages; `lb` is excluded because not all LID systems can recognize `lb`). Decision code: `other`
 - Else if the text is shorter than 50 characters, we simply choose the
   dominant language of the newspaper. Decision code: `dominant`
 - Else we apply a similar voting technique as in the global statistics step
  with the following modifications (taking into account that `impresso_ft` has
  been specifically been trained on Luxembourgish and other difficult multilingual
  cases). Decision code: `vote`
   - if the most probable language of `impresso_ft` is `lb`, the vote score of
     `impresso_ft` is set to 3*prob, using the probability of the decision  
   - the vote score of `orig_lg` is set to 2*relative support for the specific
     language (remember if greater than 75% overall)
   - the boost_factor for `impresso_ft` is applied.

    make impresso-lid-stage2-target

This is fast and cannot be run on different machines.

## Notes
To run the full lid processing on a single machine with N cores, run:

    make impresso-lid -j N 
