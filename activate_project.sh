#!/bin/bash

# Activate the virtual environment
source ./.venv/bin/activate

#clear the database model
find . -type d -name "__pycache__" -exec rm -r {} +

# Export environment variables from .env file
if [ -f .env ]; then
  # Create a temporary file to store processed environment variables
  temp_env=$(mktemp)

  # Read .env file and process multi-line variables
  while IFS= read -r line || [ -n "$line" ]; do
    # Ignore comments and empty lines
    if [[ ! $line =~ ^# ]] && [[ -n "$line" ]]; then
      # Check if the line contains an equals sign
      if [[ $line =~ '=' ]] && [[ ! $line =~ '\\$' ]]; then
        echo "$line" >> "$temp_env"
      else
        # Handle multi-line values
        multi_line_value="$line"
        while IFS= read -r next_line || [ -n "$next_line" ]; do
          multi_line_value="$multi_line_value"$'\n'"$next_line"
          if [[ ! $next_line =~ '\\$' ]]; then
            break
          fi
        done
        echo "$multi_line_value" >> "$temp_env"
      fi
    fi
  done < .env

  # Source the processed environment variables
  set -a
  source "$temp_env"
  set +a

  # Remove the temporary file
  rm -f "$temp_env"
fi