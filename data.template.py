from typing import Dict, Any

JIRA_URL = "https://jira.mysite.lan/"
EMAIL = "john.smith@website.com"
TOKEN = "TOKEN"
options: Dict[str, Any] = {
    "server": JIRA_URL,
    "verify": True,
    "timeout": 30,
}
JIRA_ONLY_STANDARD_TYPES = "issuetype in standardIssueTypes()"
REC_EPIC_KEY = 'PROJECT-1'
BOARD_ID = 4
PROJECT_ID = 1
CURRENT_SPRINT_ID = 2
NEXT_SPRINT_ID = 3

FIRSTNAMES = {'Pierre': 3, 'Paul': 1, 'Jack': 2,}

dict_statuses = {'Done': {'order': 2, 'statuses': []}, 'To Do': {'order': 0, 'statuses': []},
                     'In Progress': {'order': 1, 'statuses': []}}