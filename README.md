# mentorship-ai

Running a mentorship program across a large team or organization can be a very time-consuming exercise. In particular, the process of matching mentors and mentees in a way that provides for constructive engagement is fairly complex. Various criteria need to be taken into account such as:

- Mentee's goals and the particular areas of interest for which they're seeking mentorship-
- Mentor's areas of expertise
- Mentor's and mentee's levels (a junior level mentor shouldn't be matched with a senior level mentee)
- Their respective teams (mentors shouldn't be matched with a mentee in the same immediate team)
- Mentors can specify capacity for more than one mentee

Performing the above matching exercise can take considerable time across even a modest number of participants and doesn't scale for larger orgs.

This project aims to automate the matching process by consuming the Microsoft Forms signup data, using GPT4 to perform the matching based on the above criteria, and producing a spreadsheet of matched mentors and mentees. The model also provides a reason for each match along with an alignment score so the results can be easily validated.

The solution supports batching the participants so even very large organizations can be supported.

Future work will further automate the end-to-end process by:

- Messaging each mentor and mentee on Teams to confirm they are happy with their assigned mentor/mentee.
- If both confirm the match, then an introduction email will be sent to each mentor/mentee pair.
