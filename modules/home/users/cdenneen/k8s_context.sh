#!/bin/sh

k8s_context() {
  local path="$1"

  if [ -f $path/.context ]; then
    source $path/.context
  fi
  if [ ! $CONTEXT ]; then
    # Split the path into an array using '/' as the delimiter
    IFS='/' read -ra array <<< "$path"

    # Get the length of the array
    local length=${#array[@]}

    # Calculate the starting index to get the last three elements
    local start_index=$((length - 3))

    # Ensure start_index is not negative
    start_index=$((start_index >= 0 ? start_index : 0))

    # Extract the last three elements from the array
    last_three_elements=("${array[@]:start_index}")

    case "${last_three_elements[0]}" in
      capdev)
        ACCOUNT=dev
        ;;
      awsqa)
        ACCOUNT=qa
        ;;
      apss)
        ACCOUNT=apss
        ;;
      awsprod)
        ACCOUNT=prd
        ;;
    esac
    # echo $ACCOUNT

    case "${last_three_elements[1]}" in
      us-east-1)
        REGION=use1
        ;;
      us-west-2)
        REGION=usw2
        ;;
    esac
    # echo $REGION
    CLUSTER_ID="${last_three_elements[2]:0:7}"
    # echo $CLUSTER_ID

    CONTEXT="eks_$ACCOUNT-$REGION-$CLUSTER_ID"
  fi
  # echo $CONTEXT
  # export KUBECONFIG=$(switcher $CONTEXT)
  export KUBECONFIG=$(switcher $CONTEXT | sed 's/^__ //' | cut -d, -f1)
}
