# Information on impresso language identification (LID)

Identifying the correct language in the multilingual impresso newspaper
collection is a challenge.  Normal LID models are trained on contemporary
digital-born texts. OCRized historical newspapers often contain old and/or noisy
text. For instance, texts in Gothic fonts that were wrongly OCRized using
Antiqua font settings produce results which can be irritating for the existing
models. Especially difficult is the recognition of the mixed content of
Luxemburgish newspapers, where we find several languages in a single article. As
we need a single language category per content item, there are unsolvable
cases. For our own model, we trained specifically on items where the original
language was different from the predicted languages, and on multilingual
material from Luxembourg.

Digitized newspapers differ with respect to the metadata on the language of
their content items
  - missing information
  - partial information (e.g. no information for ads)
  - wrong information

The following steps build the necessary JSON files with the language information
 as it can be automatically determined.

## Stage 1a: Automatic Language identification

We first apply several off-the-shelve LID classifiers and our own model to the
texts. The corresponding build command is:
 
 ````make impresso-lid-stage1a-target````

This steps produces a JSON file per year per collection.

Properties of standard LIDs used in impresso:

    - langid LID (recognizes many language, incl. lb)
    - langdetect LID (recognizes many languages, except lb)
    - impresso_ft impresso model based on fasttext (supports fr/de/lb)
    - wp_ft wikipedia model delivered by fasttext (supports many languages,
      incl. lb)


## Stage 1b: Computing collection statistics

Given the incomplete and sometimes unreliable metadata regarding the content
 items'
language, we aggregate statistics per collection. This allows us a more informed
decision for the next stage of processing.

 In order to assess the dominant language of a newspaper, we compute statistics
 according to the following rules:
 
  - Content items with less than 200 characters are ignored.
  - Content items with an alphabetical rate < 0.5 are ignored.
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

 ````make impresso-lid-stage1-target````

## Stage 2: Determining the language per content item

Given the output from different LID systems and the original language
information we decide according to the following rules:

 - If the support for the original language is below 75%, we ignore it
  completely. Otherwise the original language is treated as LID system.
  - If all LID systems agree, we choose this language (restricted to de and fr
  due to the limitations of impresso_ft system). Decision code: 'all'
  - If all LID systems except impresso_ft agree on a language other than de
   or fr (typically it, la, or other rare languages). Decision code: 'others'
  - Else if the text is shorter than 50 characters, we simply choose the
   dominant language. Decision code: 'dominant'
 - Else we apply a similar voting technique as in the global statistics step
  with the following modifications (taking into account that impresso_ft has
   been specifically been trained on Luxembourgish and difficult multilingual
    cases). Decision code: 'vote'
    - if the most probable language of impresso_ft is lb, the vote score of
     impresso_ft is set to 3*prob, using the probability of the decision  
    - the vote score of orig_lg is set to 2*relative support for the specific
     language (if greater than 75%)
    - the boost_factor for impresso_ft is applied. 


