########################################################################################## 
# Makefile for impresso language identification 
#
# Note: Processing is done on locally stored data, not directly on s3 storage.


########################################################################################## 
# Make setup

SHELL := /bin/bash
export SHELLOPTS := errexit:pipefail

.SECONDARY:

# emit additional diagnostics while building
DEBUG ?= 0

# additionally print diagnostic output on terminal in debug mode

ifeq ($(DEBUG),1)
TARGET-LOG-MACRO = 2> >(tee $$@.log 1>&2)
# if you want to tee and redirect stdout and stderr, you need to write 1>2& AFTER the macro in a rule.
# Remember that the order of redirections matters! https://stackoverflow.com/q/17975232
else
TARGET-LOG-MACRO = 2> $$@.log
endif

########################################################################################## 
# Make variables for impresso data infrastructure
# Variables in uppercase and underscores can be overwritten by the user at build time

# make sure that this directory points to a local copy of the impresso s3 data containers
# only read access is needed
IMPRESSO_REBUILT_DATA_DIR ?= rebuilt-data

# Language identification version
LID_VERSION ?= v1.1

# build dir
BUILD_DIR ?= build

# write access is needed here
LID_BUILD_DIR ?= $(BUILD_DIR)/$(LID_VERSION)

# fast text models
IMPPRESSO_FASTTEXT_MODEL ?= models/fasttext/impresso-lid.bin
WIKIPEDIA_FASTTEXT_MODEL ?= models/fasttext/lid.176.bin

# minimal text length threshold for automatic LID in stage 1
MINIMAL_TEXT_LENGTH ?= 20

#CANONICAL_DIR:=/srv/scratch2/climpresso/s3data/canonical-rebuilt-release
OUTPUT_DIR ?= $(LID_BUILD_DIR)/language_identification/$(LID_VERSION)

# all known collection acronyms from the file system
COLLECTION_ACRONYMS ?= $(notdir $(wildcard $(IMPRESSO_REBUILT_DATA_DIR)/*))

ifeq ($(DEBUG),1)
$(info )
$(info VARIABLE collection-accronyms:)
$(info $(collection-accronyms))
$(info )
endif

# get path of all impresso rebuilt files
impresso-rebuilt-files := \
	$(wildcard \
		$(foreach ca,$(COLLECTION_ACRONYMS),\
			$(IMPRESSO_REBUILT_DATA_DIR)/$(ca)/*.jsonl.bz2\
		)\
	)


########################################################################################################################
# stage 1a: apply lid classification to all content items

impresso-lid-stage1-files := $(subst $(IMPRESSO_REBUILT_DATA_DIR),$(LID_BUILD_DIR)/stage1,$(impresso-rebuilt-files))

ifeq ($(DEBUG),1)
$(info )
$(info VARIABLE impresso-lid-stage1-files)
$(info $(impresso-lid-stage1-files))
$(info )
endif

impresso-lid-stage1a-target: $(impresso-lid-stage1-files)


$(LID_BUILD_DIR)/stage1/%.jsonl.bz2: IMPRESSO_REBUILT_DATA_DIR/%.jsonl.bz2
	mkdir -p $(@D) \
	&& if test -e $@.running ; \
	    then { echo "Already building $@ " && exit 0 ; } ; \
	    else { touch $@.running ; echo "Building $@ now..." ; }  ; \
	   fi \
	&& python lib/language_identification.py \
	    --impresso_ft $(IMPPRESSO_FASTTEXT_MODEL) \
	    --wp_ft $(WIKIPEDIA_FASTTEXT_MODEL) \
	    --minimal-text-length $(MINIMAL_TEXT_LENGTH) \
	    --input-file $< \
	    --output-file $@.working.jsonl.bz2 \
	    $(TARGET-LOG-MACRO) 1>&2 \
	&& mv $@.working.jsonl.bz2 $@ \
	&& rm -fv $@.running \
	|| rm -fv $@.running

# &> >(tee $@.log >&2)
# Note: we use the idiom &> >(tee $@.log >&2) because the LID systems output log differently
# https://stackoverflow.com/questions/692000/how-do-i-write-stderr-to-a-file-while-using-tee-with-a-pipe

########################################################################################################################
# Stage 1b second part: Collect lid statistics per collection

$(LID_BUILD_DIR)/stage1/%.stats.json: LID_BUILD_DIR/stage1/%/
	python lib/collection_statistics.py \
	   --collection $* \
	   --lids langid langdetect impresso_ft wp_ft \
	   --boosted_lids orig_lg impresso_ft \
	   --threshold_for_support 200 \
	   --boost_factor 1.5 \
	   --minimal_vote_score 1.5 \
	   --minimal_lid_probability 0.25 \
	   $(<)$(*)*.jsonl.bz2 \
	   > $@ \
	   2> $@.log  \
	|| { echo "Warning: Something went wrong while building $@. Check $@.log. Cleaning up $@ now." ; rm -vf $@ ; }

# collect statistics on stage 1 results per newspaper

language-identification-collection-json-files := \
  $(addprefix $(LID_BUILD_DIR)/stage1/,\
  	$(foreach ca,$(COLLECTION_ACRONYMS),$(ca).stats.json))

ifeq ($(DEBUG),1)
$(info )
$(info VARIABLE language-identification-collection-json-files)
$(info $(language-identification-collection-json-files))
$(info )
endif

# Concatenate all newspaper stats in one file
$(LID_BUILD_DIR)/stage1.stats.json: $(language-identification-collection-json-files)
	cat $+ > $@

language-identification-collection-json-target: impresso-lid-stage1a-target \
	$(language-identification-collection-json-files) \
	LID_BUILD_DIR/stage1.stats.json

impresso-lid-stage1-target: language-identification-collection-json-target

