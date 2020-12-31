##########################################################################################
# Makefile for impresso language identification
#
# Note: Processing is done on locally stored data, not directly on s3 storage.


##########################################################################################
# Make setup

SHELL := /bin/bash
export SHELLOPTS := errexit:pipefail
.SECONDARY:
.PHONY: impresso-lid impresso-lid-eval impresso-lid-stage1a-target impresso-lid-stage1b-target impresso-lid-stage2-target impresso-lid-upload-release-to-s3 impresso-lid-eval

# generally export all variables to sub-make calls (needed in this Makefile)
# The targets of stage 1a need the targets of stage 1b to exist
#export

# Note: use this target only on a single build machine
# If you run the commands on several machines on the same collection each stage has to be finished on all machines
# before moving to the next stage

#: Run full impresso LID pipeline build
impresso-lid:
	# INFO: Recursively making  impresso-lid-stage1a-target
	$(MAKE) $(MAKEFILEFLAG) -f $(firstword $(MAKEFILE_LIST))  impresso-lid-stage1a-target
	# INFO: Recursively making  impresso-lid-stage1b-target
	$(MAKE) $(MAKEFILEFLAG) -f $(firstword $(MAKEFILE_LIST))  impresso-lid-stage1b-target
	# INFO: Recursively making  impresso-lid-stage2-target
	$(MAKE) $(MAKEFILEFLAG) -f $(firstword $(MAKEFILE_LIST))  impresso-lid-stage2-target

include lib/debug.mk

# emit additional diagnostics while building
DEBUG ?= 0

# additionally print diagnostic output on terminal in debug mode

ifeq ($(DEBUG),1)
TARGET_LOG_MACRO = 2> >(tee $@.log 1>&2)
# if you want to tee and redirect stdout AND stderr, you need to write 1>2& AFTER the macro in a rule.
# Remember that the order of redirections matters! https://stackoverflow.com/q/17975232
else
TARGET_LOG_MACRO = 2> $@.log
endif

# additionally print diagnostic output on terminal in debug mode

ifeq ($(DEBUG),1)
DEBUG_OPTION = --verbose 4
else
DEBUG_OPTION =
endif

##########################################################################################
# Make variables for impresso data infrastructure
# Variables in uppercase and underscores can be overwritten by the user at build time

# make sure that this directory points to a local copy of the impresso s3 data containers
# only read access is needed
IMPRESSO_REBUILT_DATA_DIR ?= rebuilt-data

# Language identification version
LID_VERSION ?= v1.4.3

# build dir
BUILD_DIR ?= build

# write access is needed here
LID_BUILD_DIR ?= $(BUILD_DIR)/$(LID_VERSION)

# all LID systems to use 
LID_SYSTEMS ?= langid langdetect impresso_ft wp_ft

# fast text models
IMPPRESSO_FASTTEXT_MODEL ?= models/fasttext/impresso-lid.bin
WIKIPEDIA_FASTTEXT_MODEL ?= models/fasttext/lid.176.bin

# minimal text length threshold for automatic LID in stage 1 and 2
STAGE1A_MINIMAL_TEXT_LENGTH ?= 20
STAGE1B_MINIMAL_TEXT_LENGTH ?= 200
STAGE2_MINIMAL_TEXT_LENGTH ?= 50

# hyperparameters for scoring the languages
BOOST_FACTOR ?= 1.5
WEIGHT_LB_IMPRESSO ?= 6
MINIMAL_VOTING_SCORE ?= 0.5
STAGE1_MINIMAL_LID_PROBABILITY ?= 0.20
STAGE2_MINIMAL_LID_PROBABILITY ?= 0.5
MINIMAL_VOTE_SCORE ?= 1.5

# evaluation mode
EVALUATION_OUTPUT_FORMAT ?= json

# S3 bucket path (without "/" suffix)
S3_BUCKET_LANGIDENT_PATH ?= /processed-canonical-data/langident


stage2-dir := stage2

ifeq ($(EVAL_STAGE2),1)
stage2-dir := stage2-mvs$(MINIMAL_VOTING_SCORE)-mlp$(MINIMAL_LID_PROBABILITY)-wli$(WEIGHT_LB_IMPRESSO)
endif

# all known collection acronyms from the file system
COLLECTION_ACRONYMS ?= $(notdir $(wildcard $(IMPRESSO_REBUILT_DATA_DIR)/*))

# emit content of make variable if $(DEBUG) is set to 1
$(eval $(call debug_variable,COLLECTION_ACRONYMS))

# get path of all impresso rebuilt files
impresso-rebuilt-files := \
	$(wildcard \
		$(foreach ca,$(COLLECTION_ACRONYMS),\
			$(IMPRESSO_REBUILT_DATA_DIR)/$(ca)/*.jsonl.bz2\
		)\
	)


########################################################################################################################
# stage 1a: apply lid classification to all content items

impresso-lid-stage1a-files := $(subst $(IMPRESSO_REBUILT_DATA_DIR),$(LID_BUILD_DIR)/stage1,$(impresso-rebuilt-files))

$(eval $(call debug_variable,impresso-lid-stage1a-files))

#: Generate all stage 1a files
impresso-lid-stage1a-target: $(impresso-lid-stage1a-files)


$(LID_BUILD_DIR)/stage1/%.jsonl.bz2: $(IMPRESSO_REBUILT_DATA_DIR)/%.jsonl.bz2
	mkdir -p $(@D) \
	&& if test -e $@.running || test -e $@.done ; \
	    then { echo "Already building/built $@ " && exit 0 ; } ; \
	    else { echo "$${HOSTNAME}" > $@.running ; echo "$$(date -Iseconds) Building $@ now..." ; }  ; \
	   fi \
	&& trap 'rm -fv $@.running ' EXIT HUP TERM SIGINT \
	&& python lib/language_identification.py \
	    --lids $(LID_SYSTEMS) \
	    --impresso-ft $(IMPPRESSO_FASTTEXT_MODEL) \
	    --wp-ft $(WIKIPEDIA_FASTTEXT_MODEL) \
	    --minimal-text-length $(STAGE1A_MINIMAL_TEXT_LENGTH) \
	    --round-ndigits 3 \
		--git-describe $$(git describe) \
	    --infile $< \
	    --outfile $@.$${HOSTNAME}.working.jsonl.bz2 \
	    $(DEBUG_OPTION) \
	    $(TARGET_LOG_MACRO) 1>&2 \
	&& mv $@.$${HOSTNAME}.working.jsonl.bz2 $@ \
	&& mv $@.running $@.done \
	&& echo "$$(date -Iseconds) build of $@ finished successfully."

# &> >(tee $@.log >&2)
# Note: we use the idiom &> >(tee $@.log >&2) because the LID systems output log differently
# https://stackoverflow.com/questions/692000/how-do-i-write-stderr-to-a-file-while-using-tee-with-a-pipe



########################################################################################################################
# Stage 1b second part: Collect lid statistics per collection
# As stage 1a can be run in parallel on multiple machines we have to compute the successfull finishing of all stage 1a file before actually doing stage 1b

# collect statistics on stage 1a results per newspaper
impresso-lid-stage1b-files:= \
  $(addprefix $(LID_BUILD_DIR)/stage1/,\
  	$(foreach ca,$(COLLECTION_ACRONYMS),$(ca).stats.json))

$(eval $(call debug_variable,impresso-lid-stage1b-files))

# a .done stamp file for each collection to indicate completion for the next stage
impresso-lid-stage1a-done-files := $(foreach ca,$(COLLECTION_ACRONYMS),$(LID_BUILD_DIR)/stage1/$(ca).done)

# template for specifying per collection
define stage1a_done_rule_template
$(LID_BUILD_DIR)/stage1/$(ca).done : $(filter /$(ca)/,$(impresso-lid-stage1a-files))
	touch $$@

endef

$(eval $(foreach ca,$(COLLECTION_ACRONYMS),$(stage1a_done_rule_template)))


$(LID_BUILD_DIR)/stage1/%.stats.json: $(LID_BUILD_DIR)/stage1/%.done
	python lib/collection_statistics.py \
	   --collection $* \
	   --lids $(LID_SYSTEMS) \
	   --boosted-lids orig_lg impresso_ft \
	   --minimal-text-length $(STAGE1B_MINIMAL_TEXT_LENGTH) \
	   --boost-factor $(BOOST_FACTOR) \
	   --minimal-vote-score $(MINIMAL_VOTE_SCORE) \
	   --minimal-lid-probability $(STAGE1_MINIMAL_LID_PROBABILITY) \
	   --git-describe $$(git describe) \
	   $(DEBUG_OPTION) \
	   $(<:.done=)/$(*)*.jsonl.bz2 \
	   | sponge $@ \
	   $(TARGET_LOG_MACRO)  \
	&& echo "$$(date -Iseconds) build of $@ finished successfully." \
	|| { echo "Warning: Something went wrong while building $@. Check $@.log. Cleaning up $@ now." ; rm -vf $@ ; exit 1 ; }


# Concatenate all newspaper stats in one file
$(LID_BUILD_DIR)/stage1.stats.json: $(impresso-lid-stage1a-done-files) $(impresso-lid-stage1b-files)
	cat $+ > $@

#: Generate all stage 1b files
impresso-lid-stage1b-target: impresso-lid-stage1a-target \
	$(impresso-lid-stage1b-files) \
	$(LID_BUILD_DIR)/stage1.stats.json

########################################################################################################################
# Stage 2 second part: Decide for a language given collection statistics and individual content item predictions

impresso-lid-stage2-files := $(subst $(IMPRESSO_REBUILT_DATA_DIR),$(LID_BUILD_DIR)/$(stage2-dir),$(impresso-rebuilt-files))

$(eval $(call debug_variable,impresso-lid-stage2-files))

impresso-lid-stage2-diagnostics-files := $(impresso-lid-stage2-files:.jsonl.bz2=.diagnostics.json)

#: Generate all stage 2 files
impresso-lid-stage2-target: impresso-lid-stage1b-target $(impresso-lid-stage2-files) $(impresso-lid-stage2-diagnostics-files)

# rule for building all stage 2 files
$(LID_BUILD_DIR)/$(stage2-dir)/%.jsonl.bz2 $(LID_BUILD_DIR)/$(stage2-dir)/%.diagnostics.json: $(LID_BUILD_DIR)/stage1/%.jsonl.bz2
	mkdir -p $(@D) \
	&& python lib/impresso_lid.py \
	 --lids $(LID_SYSTEMS) \
	 --weight-lb-impresso-ft $(WEIGHT_LB_IMPRESSO) \
	 --minimal-lid-probability $(STAGE2_MINIMAL_LID_PROBABILITY) \
	 --minimal-voting-score $(MINIMAL_VOTING_SCORE) \
	 --minimal-text-length $(STAGE2_MINIMAL_TEXT_LENGTH) \
	 --collection-stats-filename $(patsubst %/,%.stats.json,$(subst /$(stage2-dir),/stage1,$(dir $@))) \
	 --git-describe $$(git describe) \
	 --diagnostics-json $(@:jsonl.bz2=)diagnostics.json \
	 --infile $< \
	 --outfile $@.working.jsonl.bz2 \
     $(DEBUG_OPTION) \
	 $(TARGET_LOG_MACRO) \
	&& mv $@.working.jsonl.bz2 $@ \
	&& echo "$$(date -Iseconds) build of $@ finished successfully." \
	|| { echo "Warning: Something went wrong while building $@. Check $@.log. Cleaning up $@ now." ; rm -vf $@ ; exit 1 ; }


########################################################################################################################
# Prepare official distribution for impresso with files per year

release-dir :=  $(LID_BUILD_DIR)/s3/$(LID_VERSION)

impresso-lid-release-files := $(subst $(LID_BUILD_DIR)/$(stage2-dir),$(release-dir),$(impresso-lid-stage2-files))

$(eval $(call debug_variable,impresso-lid-release-files))

#: Validate all files to be released as processed data
impresso-lid-release-target : \
	impresso-lid-stage2-target \
	$(impresso-lid-release-files)


$(release-dir)/%.jsonl.bz2: $(LID_BUILD_DIR)/$(stage2-dir)/%.jsonl.bz2
	mkdir -p $(@D) \
	&& python impresso-schemas/scripts/jsonlschema.py  \
		impresso-schemas/json/language_identification/language_identification.schema.json \
		--input-files $< \
		--output-file $@

$(release-dir)/%.diagnostics.json: $(LID_BUILD_DIR)/$(stage2-dir)/%.diagnostics.json
	cp -ua $< $@

#: Actually upload the impresso lid information to s3 impresso bucket
impresso-lid-upload-release-to-s3: impresso-lid-release-target
	rclone --verbose copy $(LID_BUILD_DIR)/s3/$(LID_VERSION) s3-impresso:$(S3_BUCKET_LANGIDENT_PATH)/$(LID_VERSION) \
	&& rclone --verbose check $(LID_BUILD_DIR)/s3/$(LID_VERSION)/ s3-impresso:$(S3_BUCKET_LANGIDENT_PATH)/$(LID_VERSION)/


########################################################################################################################
# Produce statistics

#: Compute several statistics on the output of impresso LID
impresso-lid-statistics: \
	$(LID_BUILD_DIR)/statistics.d/per-collection-year-contentitems.tsv \
	$(LID_BUILD_DIR)/statistics.d/collection-year-language-data.tsv


#: Simple check whether number of content items per collection-year pair matches other impresso processing statistics
$(LID_BUILD_DIR)/statistics.d/per-collection-year-contentitems.tsv: $(impresso-lid-stage2-diagnostics-files)
	mkdir -p $(@D) \
	&& cat $+ | jq -r '.N|to_entries[0]|[.key,.value]|@tsv' | sort | sponge $@

$(LID_BUILD_DIR)/statistics.d/collection-year-language-data.tsv: $(impresso-lid-stage2-diagnostics-files)
	cat $+ | jq -r '(.N|to_entries[0]|.key|split("-"))  as [$$collection,$$year]| (.lg|to_entries|map({key,value,$$collection,$$year})|.[]|[.collection,.year,.key,.value]|sort_by(.0,.1,.2)|@tsv)' |sort | sponge $@

########################################################################################################################
# Evaluate against gold standard

#: Perform evaluation
impresso-lid-eval: $(LID_BUILD_DIR)/$(stage2-dir).eval.all.$(EVALUATION_OUTPUT_FORMAT)

$(LID_BUILD_DIR)/$(stage2-dir).eval.all.$(EVALUATION_OUTPUT_FORMAT): impresso-lid-stage2-target
	python lib/impresso_lid_eval.py \
	< test/ground-truth/all.jsonl \
	 --file-extension jsonl.bz2 \
	 --data-dir $(LID_BUILD_DIR)/$(stage2-dir) \
	 --diagnostics-json $(@:$(EVALUATION_OUTPUT_FORMAT)=)diagnostics.jsonl \
	 --output-format $(EVALUATION_OUTPUT_FORMAT) \
	 $(DEBUG_OPTION) \
	 $(TARGET_LOG_MACRO) \
	 | sponge $@

