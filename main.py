import logging
import math
import sys
import os
import openai
import json
import pandas as pd
import requests
import math

# Constants
BATCH_SIZE = 12

# Set up OpenAI API key
openai.api_type = "azure"
openai.api_base = os.getenv("AZURE_OPENAI_ENDPOINT")
openai.api_version = "2023-05-15"
openai.api_key = os.getenv("AZURE_OPENAI_KEY")

system_message = '''
    Help pair mentees with mentors in the provided json data. Each item is either a mentor or mentee. Use the following criteria and constraints provided below:
    - Do not use mentors or mentees that aren't provided in the json data
    - Do not pair a mentor with more mentees than the mentor has capacity for.
    - Mentors and mentees should have a different manager. A mentee should also not be matched with their manager.
    - Mentors should be at least one level above mentees (as specified in the "title" field). 
    - Create pairs based on common goals or interests as defined by the objectives and details properties.

    Provide a reason/description for why you suggested each pairing as well as a reason why the match might not be ideal.
    Provide a rating out of 10 on how close the mentor's and mentee's preferences are aligned. 
    Include whether mentor is over their capacity by referencing the "mentor_capacity" property.

    Return the mentor/mentee pairs in the following JSON structure:
    [
        {
            "mentor": "mentor email",
            "mentee": "mentee email",
            "reason_for": "Reason why Mentor was paired with Mentee",
            "reason_against":"Any reasons why this might not be a good match",
            "alignment_score": "score out of 10. This must be an integer.",
            "over_capacity": "true" if mentor is over their capacity. "false" if mentor is at or below capacity e.g. mentor with capacity of 2 can be paired with 2 mentees so over_capacity would equal "false",
        },
        {
            ...
        }
    ]
    
    Think step by step and think carefully and logically.
    Ensuring no constraints have been ignored, especially the capacity constraint.

    Do not include anything other than valid json in the response. Include square brackets at the beginning and end of JSON response to ensure JSON is a valid list.
    Only include valid matches in the response.
    '''

# setup the logger to log to stdout and file
def setup_logger():
    logging.basicConfig(level=logging.DEBUG, filename='matching.log', format='%(asctime)s - %(levelname)s - %(message)s')

    root = logging.getLogger()
    # log >=DEBUG level to file
    root.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    # only log INFO and above to stdout
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    root.addHandler(handler)

# Retrieve mentor/mentee data
def retrieve_data():
    # Read in survey responses CSV files
    participants_df = pd.read_excel("responses_20_batching.xlsx")

    return participants_df

# Process the data so it can be sent to GPT, ideally into JSON structure.
def preprocess_data(participants_df):
    # Set up authentication headers
    # see https://learn.microsoft.com/en-us/graph/auth/auth-concepts#access-tokens
    access_token = os.getenv("ACCESS_TOKEN")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    print(f"Pre-processing data for {len(participants_df)} participants...")

    for index, participant in participants_df.iterrows():
        alias = participant["Email"]

        # Use Microsoft Graph API to get manager
        response = requests.get(f"https://graph.microsoft.com/v1.0/users/{alias}/manager", headers=headers)
        manager = response.json().get('userPrincipalName')
        participants_df.loc[index, "manager"] = manager
        if manager is None:
            logging.info(f"Could not find manager for {alias}")
            # exit if we can't get the manager details as it means there isn't any access token or it has expired
            exit(1)
    
        # Use Microsoft Graph API to get skip manager
        response = requests.get(f"https://graph.microsoft.com/v1.0/users/{manager}/manager", headers=headers)
        skip_manager = response.json().get('userPrincipalName')
        participants_df.loc[index, "skip_manager"] = skip_manager
        if skip_manager is None:
            logging.info(f"Could not find skip manager for {alias}")

        # Use Microsoft Graph API to get title
        response = requests.get(f"https://graph.microsoft.com/v1.0/users/{alias}", headers=headers)
        title = response.json().get('jobTitle')
        participants_df.loc[index, "title"] = title
        if title is None:
            logging.info(f"Could not find title for {alias}")
        
        #print(f"Found title {title}, manager {manager} and skip manager {skip_manager} for {alias}")

    print(f"Done pre-processing {len(participants_df)} participants")
    return participants_df

def total_mentors_in_batch(mentors_df, is_last_batch):
    current_batch_size = 0
    total_mentors = 0
    if is_last_batch:
        return len(mentors_df)
    
    for _, mentor in mentors_df.iterrows():
        current_batch_size += mentor['mentor_capacity'] + 1
        total_mentors += 1
        logging.info(f"Calculated {current_batch_size} batch size based on {mentor['Email']} and capacity {mentor['mentor_capacity']}")
        #TODO: I'll let it go above the batch size for now (+2 max)
        if current_batch_size >= BATCH_SIZE:
            break
    
    logging.info(f"Calculated {total_mentors} mentors for a total batch size of {current_batch_size}")
    return total_mentors

def update_capacity(mentors_df, matches):
    for _, match in matches.iterrows():
        mentor = mentors_df[mentors_df['Email'] == match['mentor']]
        if len(mentor) > 0:
            mentor['mentor_capacity'] -= 1
            mentors_df[mentors_df['Email'] == match['mentor']] = mentor
            logging.info(f"Updated mentor {mentor['Email'].iloc[0]} to Capacity:  {mentor['mentor_capacity'].iloc[0]}")

    return mentors_df

# Remove mentees that have been matched from the dataframe
def update_mentees(mentees_df, matches):
    for _, match in matches.iterrows():
        mentee = mentees_df[mentees_df['Email'] == match['mentee']]
        if len(mentee) > 0:
            mentees_df = mentees_df[mentees_df['Email'] != match['mentee']]
            logging.debug(f"Removed matched mentee {mentee['Email'].iloc[0]}")

    return mentees_df

# get list of unmatched mentees that have not been matched from the dataframe
def update_unmatched_mentees(mentees_df, matches):
    unmatched_mentees_df = mentees_df
    for _, match in matches.iterrows():
        mentee = mentees_df[mentees_df['Email'] == match['mentee']]
        if len(mentee) > 0:
            unmatched_mentees_df = mentees_df[mentees_df['Email'] == match['mentee']]
            logging.debug(f"Removed matched mentee {mentee['Email'].iloc[0]}")

    return unmatched_mentees_df
    
# Send message to OpenAI API and return the response
def match_with_gpt(inputdata):

    response = openai.ChatCompletion.create(
        engine = "gpt-4-32k", # use "chat" for GPT-4, "chat35" for GPT-3.5 Turbo
        messages =
            [{"role": "system", "content": system_message},
             {"role": "user", "content": f'{inputdata}'}],
    )
    completion = json.loads(str(response))

    return completion["choices"][0]["message"]["content"]

def postprocess_data(matches_df, unmatched_mentees_df):
    logging.info(f"matches: {matches_df}")
    # Delete matches file
    if os.path.isfile("matches.xlsx"):
        os.remove("matches.xlsx")
    # Write matches to Excel
    writer = pd.ExcelWriter('matches.xlsx', engine='xlsxwriter')
    matches_df.to_excel(writer, sheet_name='matched', index=False)
    unmatched_mentees_df.to_excel(writer, sheet_name='unmatched_mentees', index=False)
    writer.close()

if __name__ == '__main__':

    setup_logger()
    logging.info("Initializing...")

    participants_df = retrieve_data()
    logging.info("Data retrieved")

    inputdata = preprocess_data(participants_df)
    logging.info(f"Input data: {inputdata}")

    mentors_df = inputdata.loc[inputdata['role'] == 'Mentor']
    mentees_df = inputdata.loc[inputdata['role'] == 'Mentee']
    logging.info(f"Found {len(mentors_df)} mentors and {len(mentees_df)} mentees.")
    
    total_processed = int(0)
    total_mentees_matched = int(0)
    result_df = pd.DataFrame(columns=['mentor','mentee','reason_for','reason_against','alignment_score','over_capacity'])
    while total_processed < len(inputdata):
        # Calculate batch size, driven by the mentors capacity (assuming we'll always have less mentors)
        is_last_batch = len(inputdata) - total_processed <= BATCH_SIZE
        num_mentors_in_batch = total_mentors_in_batch(mentors_df, is_last_batch)
        num_mentees_in_batch = math.floor(mentors_df[0:num_mentors_in_batch]['mentor_capacity'].sum())
        current_batch_size = math.floor(num_mentors_in_batch + num_mentees_in_batch)

        mentors_batch = mentors_df[0:num_mentors_in_batch]
        mentees_batch = mentees_df[0:num_mentees_in_batch]
        
        batch = pd.concat([mentors_batch, mentees_batch])
        logging.info(f"-- Sending Batch of length {len(batch)}: Mentors {len(mentors_batch)}, Mentees {len(mentees_batch)}")

        # Send to gpt model 
        matches = match_with_gpt(batch.to_json())
        matches_df = pd.read_json(matches)
        
        if not is_last_batch:
            # valid_matches = matches_df[(matches_df['alignment_score'] != '') & (matches_df['alignment_score'] > 6)]
            valid_matches = matches_df[(matches_df['alignment_score'] != '') & (matches_df['alignment_score'] > 0)]
            logging.info(f"Number of matches removed due to low score: {len(matches_df) - len(valid_matches)}")
        else:
            valid_matches = matches_df

        # Update/remove mentor capacity
        mentors_df = update_capacity(mentors_df, valid_matches)
        # Update/remove mentees
        mentees_df = update_mentees(mentees_df, valid_matches)

        # Removing mentors with no capacity
        mentors_df = mentors_df[mentors_df['mentor_capacity'].astype('int') > 0]
        logging.info(f"number of mentors left with capacity: {len(mentors_df)}")

        total_processed += current_batch_size
        logging.info(f"Valid matches: {valid_matches}")
        result_df = pd.concat([result_df, valid_matches])

        if mentors_df.empty or mentees_df.empty:
            logging.info(f"Missing participants to complete the matching. Mentees: {len(mentees_df)}, Mentors {len(mentors_df)}")
            break


    unmatched_mentees_df = update_unmatched_mentees(mentees_df, result_df)
        
    postprocess_data(result_df, unmatched_mentees_df)
    logging.info("Finished")