import base64
import difflib
import threading
import urllib.parse

from pathlib import Path
from typing import Tuple

from nornir.core.task import Optional, Result, Task

import requests


LOCK = threading.Lock()


def _generate_diff(original: str, fromfile: str, tofile: str, content: str) -> str:
    diff = difflib.unified_diff(
        original.splitlines(), content.splitlines(), fromfile=fromfile, tofile=tofile
    )
    return "\n".join(diff)


def _get_repository(session: requests.Session, url: str, repository: str) -> int:
    resp = session.get(f"{url}/api/v4/projects?search={repository}")
    if resp.status_code != 200:
        raise RuntimeError(f"Unexpected Gitlab status code {resp.status_code}")

    pid = 0
    found = False
    respjson = resp.json()
    if not len(respjson):
        raise RuntimeError("Gitlab repository not found")

    for p in respjson:
        if p.get("name", "") == repository:
            found = True
            pid = p.get("id", 0)

    if not pid or not found:
        raise RuntimeError("Gitlab repository not found")

    return pid


def _remote_exists(
    task: Task, session: requests.Session, url: str, pid: int, file_path: str, ref: str
) -> Tuple[bool, str]:
    file_path = urllib.parse.quote(file_path, safe="")
    resp = session.get(
        f"{url}/api/v4/projects/{pid}/repository/files/{file_path}?ref={ref}"
    )
    if resp.status_code == 200:
        return (
            True,
            base64.decodebytes(resp.json()["content"].encode("ascii")).decode(),
        )
    return (False, "")


def _local_exists(task: Task, file_path: str) -> Tuple[bool, str]:
    try:
        with open(Path(file_path)) as f:
            content = f.read()
        return (True, content)
    except FileNotFoundError:
        return (False, "")


def _create(
    task: Task,
    session: requests.Session,
    url: str,
    pid: int,
    file_path: str,
    content: str,
    branch: str,
    commit_message: str,
    dry_run: bool,
) -> str:
    if dry_run:
        return _generate_diff("", "", file_path, content)

    quoted_file_path = urllib.parse.quote(file_path, safe="")
    with LOCK:
        url = f"{url}/api/v4/projects/{pid}/repository/files/{quoted_file_path}"
        data = {"branch": branch, "content": content, "commit_message": commit_message}
        resp = session.post(url, data=data)

        if resp.status_code != 201:
            raise RuntimeError(f"Unable to create file: {file_path}!")
    return _generate_diff("", "", file_path, content)


def _update(
    task: Task,
    session: requests.Session,
    url: str,
    pid: int,
    file_path: str,
    content: str,
    branch: str,
    commit_message: str,
    dry_run: bool,
) -> str:
    exists, original = _remote_exists(task, session, url, pid, file_path, branch)

    if not exists:
        raise RuntimeError(f"File '{file_path}' does not exist!")

    if dry_run:
        return _generate_diff(original, file_path, file_path, content)

    quoted_file_path = urllib.parse.quote(file_path, safe="")
    if original != content:
        with LOCK:
            url = f"{url}/api/v4/projects/{pid}/repository/files/{quoted_file_path}"
            data = {
                "branch": branch,
                "content": content,
                "commit_message": commit_message,
            }
            resp = session.put(url=url, data=data)
            if resp.status_code != 200:
                raise RuntimeError(f"Unable to update file: {file_path}")
    return _generate_diff(original, file_path, file_path, content)


def _get(
    task: Task,
    session: requests.Session,
    url: str,
    pid: int,
    file_path: str,
    destination: str,
    ref: str,
    dry_run: bool,
) -> str:

    # if destination is not provided, use the filename as destination in current
    # directory
    if destination == "":
        destination = file_path

    (_, local) = _local_exists(task, destination)

    (status, content) = _remote_exists(task, session, url, pid, file_path, ref)

    if not status:
        raise RuntimeError(f"Unable to get file: {file_path}")

    if not dry_run:
        if local != content:
            with open(destination, "w") as f:
                f.write(content)

    return _generate_diff(local, destination, destination, content)

def gitlab_get(
    task: Task,
    url: str,
    token: str,
    repository: str,
    file_path: str,
    destination: str,
    ref: str="master",
    dry_run: bool=False,
) -> Result:
    
    session = requests.session()
    session.headers.update({"PRIVATE-TOKEN": token})

    pid = _get_repository(session, url, repository)

    diff = _get(
        task=task,
        session=session,
        url=url,
        pid=pid,
        file_path=file_path,
        destination=destination,
        ref=ref,
        dry_run=dry_run
    )

    return Result(host=task.host, diff=diff, changed=bool(diff))
    
def gitlab_exists(
    task: Task,
    url: str,
    token: str,
    repository: str,
    file_path: str,
    ref: str="master",
) -> Result:
    """
    Checks if a file exists in a repository
    """
    session = requests.session()
    session.headers.update({"PRIVATE-TOKEN": token})

    pid = _get_repository(session, url, repository)

    (status, content) = _remote_exists(task, session, url, pid, file_path, ref)

    return Result(host=task.host, result = status)

def gitlab_update(
    task: Task,
    url: str,
    token: str,
    repository: str,
    file_path: str,
    content: str,
    branch: str="master",
    dry_run: bool=False, 
    commit_message: str=""
) -> Result:
    """
    Updates a file in a giltab repository
    """
    session = requests.session()
    session.headers.update({"PRIVATE-TOKEN": token})

    pid = _get_repository(session, url, repository)

    diff = _update(task, session, url, pid, file_path, content, branch, commit_message, dry_run)
        
    return Result(host=task.host, diff=diff, changed=bool(diff))
        
def gitlab_create(
    task: Task,
    url: str,
    token: str,
    repository: str,
    file_path: str,
    content: str,
    dry_run: bool = False,
    branch: str = "master",
    commit_message: str ="",
    
) -> Result:
    """
    Creates a file in a gitlab repository
    """

    session = requests.session()
    session.headers.update({"PRIVATE-TOKEN": token})

    pid = _get_repository(session, url, repository)

    diff = _create(task, session, url, pid, file_path, content, branch, commit_message, dry_run)

    return Result(host=task.host, diff = diff, changed=bool(diff))
    
#def gitlab(
    #task: Task,
    #url: str,
    #token: str,
    #repository: str,
    #filename: str,
    #content: str = "",
    #action: str = "create",
    #dry_run: Optional[bool] = None,
    #branch: str = "master",
    #destination: str = "",
    #ref: str = "master",
    #commit_message: str = "",
#) -> Result:
    #"""
    #Exposes some of the Gitlab API functionality for operations on files
    #in a Gitlab repository.
#
    #Example:
#
        #nornir.run(files.gitlab,
                   #action="create",
                   #url="https://gitlab.localhost.com",
                   #token="ABCD1234",
                   #repository="test",
                   #filename="config",
                   #ref="master")
#
    #Arguments:
        #dry_run: Whether to apply changes or not
        #url: Gitlab instance URL
        #token: Personal access token
        #repository: source/destination repository
        #filename: source/destination file name
        #content: content to write
        #action: ``create``, ``update``, ``get``
        #branch: destination branch
        #destination: local destination filename (only used in get action)
        #ref: branch, commit hash or tag (only used in get action)
        #commit_message: commit message
#
    #Returns:
        #Result object with the following attributes set:
            #* changed (``bool``):
            #* diff (``str``): unified diff
#
    #"""
    #dry_run = dry_run if dry_run is not None else task.is_dry_run()
#
    #session = requests.session()
    #session.headers.update({"PRIVATE-TOKEN": token})
#
    #if commit_message == "":
        #commit_message = "File created with nornir"
#
    #pid = _get_repository(session, url, repository)
#
    #if action == "create":
        #diff = _create(
            #task, session, url, pid, filename, content, branch, commit_message, dry_run
        #)
    #return Result(host=task.host, diff=diff, changed=bool(diff))

