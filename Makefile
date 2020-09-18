########################################################################################## 
# Makefile for impresso language identification 
#
# Note: Processing is done on local data. Not directly on s3 storage.


########################################################################################## 
# Make setup

SHELL:=/bin/bash
export SHELLOPTS:=errexit:pipefail

.SECONDARY:

# emit additional diagnostics
DEBUG ?= 0


########################################################################################## 
# Make variables for impresso data infrastructure 

# make sure that this directory points to a local copy of the impresso s3 data
# only read access is needed
IMPRESSO-REBUILT-DATA-DIR ?= rebuilt-data

# Language identification version
LID-VERSION ?= v1.1



# write access is needed
LID-BUILD-DIR ?= build/$(LID-VERSION)


# fast text models

IMPPRESSO-FASTTEXT-MODEL ?= models/fasttext/impresso-lid.bin
WIKIPEDIA-FASTTEXT-MODEL ?= models/fasttext/lid.176.bin



#CANONICAL_DIR:=/srv/scratch2/climpresso/s3data/canonical-rebuilt-release
OUTPUT_DIR:=$(LID-BUILD-DIR)/language_identification/$(VERSION)

impresso-rebuilt-files := $(wildcard $(IMPRESSO-REBUILT-DATA-DIR)/*/*.jsonl.bz2)
impresso-lid-stage1-files := $(subst $(IMPRESSO-REBUILT-DATA-DIR),$(LID-BUILD-DIR)/stage1,$(impresso-rebuilt-files))


ifeq ($(DEBUG),1)
$(info )
$(info VARIABLE impresso-lid-stage1-files: )
$(info $(impresso-lid-stage1-files))
$(info )
endif

impresso-lid-stage1-target : $(impresso-lid-stage1-files)

$(LID-BUILD-DIR)/stage1/%.jsonl.bz2: $(IMPRESSO-REBUILT-DATA-DIR)/%.jsonl.bz2
	mkdir -p $(@D) \
	&& if test -e $@.running ; then { echo "Already building $@ " && exit 0 ; } ; else  { touch $@.running  ; echo "Building $@ now..." ; }  ; fi  \
	&& python lib/language_identification.py \
	   --impresso_ft $(IMPPRESSO-FASTTEXT-MODEL) \
	   --wp_ft $(WIKIPEDIA-FASTTEXT-MODEL) \
	   -i $< -o $@.working.jsonl.bz2  &> >(tee $@.log >&2)  \
	&& mv $@.working.jsonl.bz2 $@ \
	&& rm -fv $@.running $@.working.jsonl.bz2



$(OUTPUT_DIR)-stage1/%.stats.json: $(OUTPUT_DIR)-stage1/%/
	bzcat $(<)$**.jsonl.bz2| python lib/language_identification_collection_stats.py -C $* 2> $@.log > $@ || rm -f $@

