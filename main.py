from __future__ import annotations

from jira.exceptions import JIRAError
import webbrowser
import urllib.parse
from capacitysheet import create_xlsx
from data import JIRA_URL, TOKEN, JIRA_ONLY_STANDARD_TYPES, CURRENT_SPRINT_ID, BOARD_ID, dict_statuses
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


    sprints = jm.list_sprints(BOARD_ID, state="active")
    print("Sprints:", len(sprints))
    sprint = jm.get_sprint(CURRENT_SPRINT_ID)
    sprint_id = sprint["id"]
    report = jm.get_sprint_report(sprint_id, jql_extra=JIRA_ONLY_STANDARD_TYPES)
    print("\nSprint:", report["sprint"])
    tickets = report["tickets"]
    print("Tickets:", len(tickets))
    jqls = []
    for t in tickets:
        print(t)
    for name, data in sorted(dict_statuses.items(), key=lambda item: item[1]["order"]):
        print(name)
        statuses = ",".join([f'"{s["name"]}"' for s in data['statuses']])
        jql = f"sprint = {CURRENT_SPRINT_ID} and status in ({statuses}) and {JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
        print(jql)
        jqls.append(jql)
        # for i in jm.run_jql(jql):
        #    print(i)
    print("FASTLANE")
    jql = f"sprint = {CURRENT_SPRINT_ID} and label in ('fastlane', 'Fastlane') and {JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
    print(jql)
    jqls.append(jql)

    # for i in jm.run_jql(jql):
    #    print(i)
    open_jira_tabs(JIRA_URL, jqls)
    print("CAPACITY SHEET")
    create_xlsx("stats.xlsx", jm)
except JIRAError as e:
    print("JIRAError:", e)
except Exception as e:
    print("Error:", type(e).__name__, e)
