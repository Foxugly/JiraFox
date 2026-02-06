from __future__ import annotations

import urllib.parse
import webbrowser

from datetime import datetime
from jira.exceptions import JIRAError

from capacitysheet import create_xlsx
from data import JIRA_URL, TOKEN, JIRA_ONLY_STANDARD_TYPES, BOARD_ID, dict_statuses
from jiramanager import JiraManager


def open_jira_tabs(jira_base_url: str, jql_list: list[str]):
    for jql in jql_list:
        encoded = urllib.parse.quote(jql)
        url = f"{jira_base_url}/issues/?jql={encoded}"
        webbrowser.open_new_tab(url)


jm = JiraManager(JIRA_URL, TOKEN, verify=True)

try:
    jm.connect()
    print("Connected:", jm.jira.myself().get("displayName"))

    for s in jm.list_statuses():
        dict_statuses[s['statusCategory']]['statuses'].append(s)
    current_sprint = jm.get_current_sprint(BOARD_ID)
    current_sprint_id = current_sprint.get("id")
    jqls = []
    for name, data in sorted(dict_statuses.items(), key=lambda item: item[1]["order"]):
        print(name)
        statuses = ",".join([f'"{s["name"]}"' for s in data['statuses']])
        jql = f"sprint = {current_sprint_id} and status in ({statuses}) and {JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
        print(jql)
        jqls.append(jql)
    print("FASTLANE")
    jql = f"sprint = {current_sprint_id} and label in ('fastlane', 'Fastlane') and {JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
    print(jql)
    jqls.append(jql)

    # open_jira_tabs(JIRA_URL, jqls)
    print("CAPACITY SHEET")
    prefix = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f" {prefix}_{current_sprint.get("name")}.xlsx".replace(" ","").replace("/","_")
    create_xlsx(filename, jm)
except JIRAError as e:
    print("JIRAError:", e)
except Exception as e:
    print("Error:", type(e).__name__, e)
