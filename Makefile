##########################################################################################
# Makefile for impresso language identification
#
# Note: Processing is done on locally stored data, not directly on s3 storage.


##########################################################################################
# Make setup

SHELL := /bin/bash
export SHELLOPTS := errexit:pipefail
.SECONDARY:

# generally export all variables to sub-make calls (needed in this Makefile)
# The targets of stage 1a need the targets of stage 1b to exist
#export

# Note: use this target only on a single build machine
# If you run the commands on several machines on the same collection each stage has to be finished on all machines
# before moving to the next stage
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

#
OUTPUT_DIR ?= $(LID_BUILD_DIR)/language_identification/$(LID_VERSION)

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

impresso-lid-stage1a-target: $(impresso-lid-stage1a-files)


$(LID_BUILD_DIR)/stage1/%.jsonl.bz2: $(IMPRESSO_REBUILT_DATA_DIR)/%.jsonl.bz2
	mkdir -p $(@D) \
	&& if test -e $@.running ; \
	    then { echo "Already building $@ " && exit 0 ; } ; \
	    else { touch $@.running ; echo "$$(date -Iseconds) Building $@ now..." ; }  ; \
	   fi \
	&& python lib/language_identification.py \
	    --impresso-ft $(IMPPRESSO_FASTTEXT_MODEL) \
	    --wp-ft $(WIKIPEDIA_FASTTEXT_MODEL) \
	    --minimal-text-length $(MINIMAL_TEXT_LENGTH) \
	    --infile $< \
	    --outfile $@.working.jsonl.bz2 \
	    $(DEBUG_OPTION) \
	    $(TARGET_LOG_MACRO) 1>&2 \
	&& mv $@.working.jsonl.bz2 $@ \
	&& rm -fv $@.running \
	&& echo "$$(date -Iseconds) build of $@ finished successfully." \
	|| rm -fv $@.running

# &> >(tee $@.log >&2)
# Note: we use the idiom &> >(tee $@.log >&2) because the LID systems output log differently
# https://stackoverflow.com/questions/692000/how-do-i-write-stderr-to-a-file-while-using-tee-with-a-pipe

########################################################################################################################
# Stage 1b second part: Collect lid statistics per collection

# collect statistics on stage 1a results per newspaper
impresso-lid-stage1b-files:= \
  $(addprefix $(LID_BUILD_DIR)/stage1/,\
  	$(foreach ca,$(COLLECTION_ACRONYMS),$(ca).stats.json))

$(eval $(call debug_variable,impresso-lid-stage1b-files))


$(LID_BUILD_DIR)/stage1/%.stats.json: $(LID_BUILD_DIR)/stage1/%/
	python lib/collection_statistics.py \
	   --collection $* \
	   --lids langid langdetect impresso_ft wp_ft \
	   --boosted-lids orig_lg impresso_ft \
	   --minimal-text-length 200 \
	   --boost-factor 1.5 \
	   --minimal-vote-score 1.5 \
	   --minimal-lid-probability 0.25 \
	   $(DEBUG_OPTION) \
	   $(<)$(*)*.jsonl.bz2 \
	   > $@ \
	   $(TARGET_LOG_MACRO)  \
	&& echo "$$(date -Iseconds) build of $@ finished successfully." \
	|| { echo "Warning: Something went wrong while building $@. Check $@.log. Cleaning up $@ now." ; rm -vf $@ ; }


# Concatenate all newspaper stats in one file
$(LID_BUILD_DIR)/stage1.stats.json: $(impresso-lid-stage1b-files)
	cat $+ > $@

impresso-lid-stage1b-target: impresso-lid-stage1a-target \
	$(impresso-lid-stage1b-files) \
	$(LID_BUILD_DIR)/stage1.stats.json

########################################################################################################################
# Stage 2 second part: Decide for a language given collection statistics and individual content item predictions

impresso-lid-stage2-files := $(subst $(IMPRESSO_REBUILT_DATA_DIR),$(LID_BUILD_DIR)/stage2,$(impresso-rebuilt-files))

$(eval $(call debug_variable,impresso-lid-stage2-files))

impresso-lid-stage2-target: impresso-lid-stage1b-target $(impresso-lid-stage2-files)

# python lib/impresso_lid.py -i testbuild/v1.1/stage1/waeschfra/waeschfra-1871.jsonl.bz2 -C testbuild/v1.1/stage1/waeschfra.stats.json  --lids langdetect langid orig_lg impresso_ft wp_ft  --boosted_lids impresso_ft --double_boosted_lids impresso_ft -v 4
# rule for building all stage 2 files
$(LID_BUILD_DIR)/stage2/%.jsonl.bz2: $(LID_BUILD_DIR)/stage1/%.jsonl.bz2
	mkdir -p $(@D) \
	&& python lib/impresso_lid.py \
	 --lids langid langdetect impresso_ft wp_ft \
	 --weight-lb-impresso-ft 3
	 --minimal-text-length $(MINIMAL_TEXT_LENGTH) \
	 --collection-json-stats $(patsubst %/,%.stats.json,$(subst /stage2,/stage1,$(dir $@))) \
	 --infile $< \
	 --outfile $@.working.jsonl.bz2 \
     $(DEBUG_OPTION) \
	 $(TARGET_LOG_MACRO) \
	&& mv $@.working.jsonl.bz2 $@ \
	&& echo "$$(date -Iseconds) build of $@ finished successfully." \
	|| { echo "Warning: Something went wrong while building $@. Check $@.log. Cleaning up $@ now." ; rm -vf $@ ; }
