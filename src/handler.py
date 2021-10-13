import http.client
import json
import os

from atlassian import bitbucket


def verify_event(event: dict) -> bool:
    """
    Function to verify whether the lambda got invoked by correct BitBucket event.

    :param event: JSON dict sent from Bitbucket.
    :return: Bool.
    """
    if type(event) is not dict:
        return False

    if "pullrequest" not in event:
        return False

    if "type" not in event["pullrequest"] or event["pullrequest"]["type"] != "pullrequest":
        return False

    if "state" not in event["pullrequest"] or event["pullrequest"]["state"] != "MERGED":
        return False

    return True


def get_repo_details(event: dict) -> dict:
    """
    Get necessary data to be able to make API calls to BitBucket.

    :param event: JSON dict sent from Bitbucket.
    :return: Dict. Dictionary with repo details.
    """
    return {
        "workspace_uuid": event["repository"]["workspace"]["uuid"],
        "repository_uuid": event["repository"]["uuid"],
        "pr_dst_branch": event["pullrequest"]["destination"]["branch"]["name"],
    }


def get_open_pr_branches(bitbucket_client: bitbucket.Cloud, repo_details: dict) -> list:
    """
    Get list of branches that have open PR to the same destination branch as the merged PR had.

    :param bitbucket_client: bitbucket.Cloud. Atlassian api client implementation for Bitbucket Cloud.
    :param repo_details: Dictionary with repo details.
    :return: List of branches with open PRs.
    """
    url_path = f"repositories/{repo_details['workspace_uuid']}/{repo_details['repository_uuid']}/pullrequests"
    try:
        open_pr = bitbucket_client.get(url_path)
    except Exception:
        print("[ERROR] Failed to fetch open PR branches on bitbucket.")
        return None
    open_pr_branches = []
    for pr in open_pr["values"]:
        if pr["destination"]["branch"]["name"] == repo_details["pr_dst_branch"]:
            open_pr_branches.append(pr["source"]["branch"]["name"])
    return open_pr_branches


def trigger_new_pipeline(circleci_client: http.client, branch: str) -> None:
    """
    Make an API call to CircleCI to trigger new pipeline for specific project/branch.

    :param circleci_client: http.client. HTTP connection to circleci.com.
    :param branch: str. Name of a branch where new pipeline has to be triggered.
    :return: None.
    """
    api_path = f"/api/v2/project/{os.environ.get('CIRCLECI_PROJECT_SLUG')}/pipeline"
    payload = '{"branch":"BRANCH"}'.replace("BRANCH", branch)
    headers = {"content-type": "application/json", "Circle-Token": os.environ.get("CIRCLECI_API_TOKEN")}
    return circleci_client.request("POST", api_path, payload, headers)


def lambda_handler(event, context):
    """
    Main function.

    :param event: JSON dict sent from Bitbucket.
    :param context: dict. Execution environment details.
    :return: None or statusCode.
    """
    try:
        event = json.loads(event["body"])
    except TypeError:
        print("[ERROR] No or invalid JSON received.")
        return {"statusCode": http.HTTPStatus.BAD_REQUEST}

    if not verify_event(event):
        print("[ERROR] Received incorrect BitBucket event OR malformed JSON payload.")
        return {"statusCode": http.HTTPStatus.BAD_REQUEST}

    try:
        bitbucket_client = bitbucket.Cloud(
            url=os.environ.get("BITBUCKET_API_URL"),
            username=os.environ.get("BITBUCKET_USERNAME"),
            password=os.environ.get("BITBUCKET_APP_PASSWORD"),
            cloud=True,
        )
    except Exception:
        print("[ERROR] Failed to initialize bitbucket client.")
        return {"statusCode": http.HTTPStatus.INTERNAL_SERVER_ERROR}

    repo_details = get_repo_details(event)
    relevant_branches = get_open_pr_branches(bitbucket_client, repo_details)
    if relevant_branches is None:
        return {"statusCode": http.HTTPStatus.INTERNAL_SERVER_ERROR}

    if len(relevant_branches) == 0:
        print("[INFO] No open PR branches detected.")
        return None

    status_code = http.HTTPStatus.OK
    for branch in relevant_branches:
        conn = http.client.HTTPSConnection("circleci.com", timeout=5.0)
        try:
            trigger_new_pipeline(conn, branch)
            print(f"[INFO] Triggered new pipeline on branch: {branch}.")
        except http.client.HTTPException:
            print(f"[ERROR] Failed to trigger new pipeline on branch: {branch}.")
            status_code = http.HTTPStatus.INTERNAL_SERVER_ERROR
    return {"statusCode": status_code}
