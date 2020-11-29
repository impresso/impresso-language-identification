# some macros for makefile debugging mode

# prints out the name of a variable and then
# $(call debug_variable,VARIABLENAME)
define debug_variable
ifeq ($(DEBUG),1)
$(info DEBUG MAKE VARIABLE '$1' = $($1) )
endif
endef
