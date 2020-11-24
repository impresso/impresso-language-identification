# impresso-language-identification

## Global statistics

Some newspapers differ with respect to the metadata on the language of their content items
  - No information at all
  - Partial information (e.g. no information for ads)
  - Wrong information
  
 In order to assess the dominant languages of newspapers, we compute statistics for each newspaper according to the following rules:
 
  - Content items with less than 200 characters are ignored.
  - Content items with an alphabetical rate < 0.5 are ignored.
  - Every language identification (LID) prediction has one vote.
  - If external metadata exists (called `orig_lg` henceforth), it counts also as a LID prediction.
  - If the `impresso_ft` or the `orig_lg` vote has support from another LID model, their votes are boosted by 1.5.
  - The language with the most votes wins and is counted. In case of a tie, we don't count.
