# Source this file in bash to get a short chat command:
#   source scripts/bash_aliases.sh
#
# Bash treats "/chat" as an absolute path, so the portable command is "chat".
# If your bash accepts slash aliases, the second alias gives you the requested feel too.

alias chat='python cli.py chat'
alias /chat='python cli.py chat'

