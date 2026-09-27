#!/usr/bin/env python3
"""commitlint: 提交信息与变更范围规范检查（仅标准库）。

用法:
  python3 commitlint.py check --git HEAD [--rules rules.json]
  python3 commitlint.py check --message-file msg.txt --files a.py b.py --author-email dev@example.com
  python3 commitlint.py evaluate --dataset labeled_commits.json [--report report.txt]

退出码:
  0  检查通过 / 对拍全部一致
  1  存在违规 / 对拍存在不一致
  2  用法或配置错误
"""
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

SUBJECT_RE = re.compile(
    r"^(?P<type>[a-z]+)(\((?P<scope>[a-z0-9._/-]+)\))?: (?P<desc>.*)$"
)


@dataclass
class Commit:
    message: str
    files: list = field(default_factory=list)
    author_name: str = ""
    author_email: str = ""
    ref: str = ""

    @property
    def subject(self):
        return self.message.splitlines()[0] if self.message.splitlines() else ""

    @property
    def body(self):
        lines = self.message.splitlines()
        return "\n".join(lines[1:]) if len(lines) > 1 else ""


@dataclass
class Finding:
    code: str
    detail: str


@dataclass
class Result:
    commit: Commit
    status: str  # "pass" | "fail" | "exempt"
    findings: list = field(default_factory=list)
    exemption: dict = None


def load_rules(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rules = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("error: cannot load rules file %s: %s\n" % (path, exc))
        raise SystemExit(EXIT_ERROR)
    for key in ("types", "scopes", "description", "exemptions"):
        if key not in rules:
            sys.stderr.write("error: rules file missing key %r\n" % key)
            raise SystemExit(EXIT_ERROR)
    return rules


def match_exemption(commit, rules):
    """返回命中的豁免规则（可审计），未命中返回 None。"""
    exemptions = rules.get("exemptions", {})

    merge = exemptions.get("merge", {})
    if merge.get("enabled", False):
        for prefix in merge.get("subject_prefixes", []):
            if commit.subject.startswith(prefix):
                return {"rule": "merge",
                        "reason": "subject starts with %r" % prefix}

    revert = exemptions.get("revert", {})
    if revert.get("enabled", False):
        sp = revert.get("subject_pattern", r"^Revert \"")
        bp = revert.get("body_pattern", r"This reverts commit [0-9a-f]+")
        if re.search(sp, commit.subject) and re.search(bp, commit.body):
            return {"rule": "revert",
                    "reason": "subject matches %r and body matches %r" % (sp, bp)}

    bot = exemptions.get("bot", {})
    if bot.get("enabled", False):
        for pat in bot.get("author_email_patterns", []):
            if fnmatch.fnmatchcase(commit.author_email.lower(), pat.lower()):
                return {"rule": "bot",
                        "reason": "author email %r matches %r"
                                  % (commit.author_email, pat)}
        for pat in bot.get("author_name_patterns", []):
            if fnmatch.fnmatchcase(commit.author_name.lower(), pat.lower()):
                return {"rule": "bot",
                        "reason": "author name %r matches %r"
                                  % (commit.author_name, pat)}
    return None


def path_in_scope(path, patterns):
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatchcase(path, pat):
            return True
    return False


def check_commit(commit, rules):
    """对单个提交执行全部规则，返回 Result。"""
    exemption = match_exemption(commit, rules)
    if exemption is not None:
        return Result(commit=commit, status="exempt", exemption=exemption)

    findings = []
    desc_rules = rules["description"]
    scopes = rules["scopes"]

    match = SUBJECT_RE.match(commit.subject)
    if match is None:
        findings.append(Finding(
            "bad-format",
            "subject must look like 'type(scope): description', got %r"
            % commit.subject))
        # 结构都不合法时跳过后续结构类检查，但空提交检查仍有效。
    else:
        ctype = match.group("type")
        scope = match.group("scope")
        desc = match.group("desc")

        if ctype not in rules["types"]:
            findings.append(Finding(
                "bad-type",
                "type %r not in allowed types %s"
                % (ctype, ", ".join(rules["types"]))))

        if scope is None:
            if rules.get("require_scope", False):
                findings.append(Finding("missing-scope", "scope is required"))
        elif scope not in scopes:
            findings.append(Finding(
                "unknown-scope",
                "scope %r not declared in rules" % scope))

        if not desc.strip():
            findings.append(Finding("empty-description",
                                    "description is empty"))
        max_len = desc_rules.get("max_length")
        if max_len is not None and len(desc) > max_len:
            findings.append(Finding(
                "description-too-long",
                "description is %d chars, max is %d" % (len(desc), max_len)))
        if desc_rules.get("ascii_only", False):
            try:
                desc.encode("ascii")
            except UnicodeEncodeError:
                findings.append(Finding(
                    "non-ascii-description",
                    "description contains non-ASCII characters"))

        # 变更范围一致性：声明了 scope 时，改动文件必须落在该 scope 内。
        if scope is not None and scope in scopes and commit.files:
            allowed = list(scopes[scope]) + rules.get("shared_files", [])
            for path in commit.files:
                if not path_in_scope(path, allowed):
                    findings.append(Finding(
                        "file-out-of-scope",
                        "file %r is outside declared scope %r" % (path, scope)))

    if not commit.files and rules.get("require_changes", True):
        findings.append(Finding("empty-commit", "commit changes no files"))

    status = "fail" if findings else "pass"
    return Result(commit=commit, status=status, findings=findings)


def commit_from_git(ref):
    """从本地 git 仓库读取提交信息、作者与改动文件。"""
    def git(*args):
        proc = subprocess.run(
            ["git"] + list(args), capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write("error: git %s failed: %s"
                             % (" ".join(args), proc.stderr.strip()))
            raise SystemExit(EXIT_ERROR)
        return proc.stdout

    fmt = git("log", "-1", "--format=%an%n%ae%n%B", ref)
    parts = fmt.split("\n", 2)
    if len(parts) < 3:
        sys.stderr.write("error: cannot parse git log output for %s\n" % ref)
        raise SystemExit(EXIT_ERROR)
    name, email, message = parts[0], parts[1], parts[2].rstrip("\n")
    out = git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", ref)
    files = [line for line in out.splitlines() if line.strip()]
    return Commit(message=message, files=files,
                  author_name=name, author_email=email, ref=ref)


def render_result(result):
    commit = result.commit
    label = commit.ref or (commit.subject[:40] or "<empty message>")
    lines = []
    if result.status == "exempt":
        lines.append("[EXEMPT] %s" % label)
        lines.append("  exemption rule: %s (%s)"
                     % (result.exemption["rule"], result.exemption["reason"]))
    elif result.status == "pass":
        lines.append("[PASS]   %s" % label)
    else:
        lines.append("[FAIL]   %s" % label)
        for finding in result.findings:
            lines.append("  - %s: %s" % (finding.code, finding.detail))
    return lines


def summarize(results):
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    exempt = sum(1 for r in results if r.status == "exempt")
    return ("summary: %d checked, %d pass, %d fail, %d exempt"
            % (len(results), passed, failed, exempt))


def cmd_check(args):
    rules = load_rules(args.rules)
    if args.git:
        commit = commit_from_git(args.git)
    else:
        message = args.message or ""
        if args.message_file:
            with open(args.message_file, "r", encoding="utf-8") as fh:
                message = fh.read().rstrip("\n")
        commit = Commit(message=message, files=args.files or [],
                        author_name=args.author_name or "",
                        author_email=args.author_email or "",
                        ref=args.ref or "")
    result = check_commit(commit, rules)
    for line in render_result(result):
        print(line)
    print(summarize([result]))
    return EXIT_VIOLATION if result.status == "fail" else EXIT_OK


def cmd_evaluate(args):
    rules = load_rules(args.rules)
    try:
        with open(args.dataset, "r", encoding="utf-8") as fh:
            dataset = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write("error: cannot load dataset: %s\n" % exc)
        return EXIT_ERROR

    lines = ["commitlint evaluate report", "=" * 24, ""]
    true_pos = true_neg = false_pos = false_neg = 0
    mismatches = []

    for entry in dataset.get("commits", []):
        commit = Commit(
            message=entry.get("message", ""),
            files=entry.get("files", []),
            author_name=entry.get("author_name", ""),
            author_email=entry.get("author_email", ""),
            ref=entry.get("id", ""),
        )
        result = check_commit(commit, rules)
        script_pass = result.status in ("pass", "exempt")
        human_pass = entry.get("human_label") == "pass"

        if script_pass and human_pass:
            true_neg += 1
        elif not script_pass and not human_pass:
            true_pos += 1
        elif not script_pass and human_pass:
            false_pos += 1  # 误报：脚本判违规，人工判通过
        else:
            false_neg += 1  # 漏报：脚本判通过，人工判违规

        mark = "OK " if script_pass == human_pass else "MISMATCH"
        lines.append("[%s] %s  script=%s human=%s"
                     % (mark, commit.ref or "<no-id>",
                        "pass" if script_pass else "fail",
                        entry.get("human_label", "?")))
        if result.status == "exempt":
            lines.append("       exempt via %s: %s"
                         % (result.exemption["rule"],
                            result.exemption["reason"]))
        for finding in result.findings:
            lines.append("       - %s: %s" % (finding.code, finding.detail))
        if script_pass != human_pass:
            mismatches.append(commit.ref)
            note = entry.get("note")
            if note:
                lines.append("       human note: %s" % note)

    total = true_pos + true_neg + false_pos + false_neg
    lines += [
        "",
        "confusion matrix (positive = 脚本判定违规)",
        "  命中 true-positive  (双方都判违规): %d" % true_pos,
        "  正确 true-negative  (双方都判通过): %d" % true_neg,
        "  误报 false-positive (脚本违规/人工通过): %d" % false_pos,
        "  漏报 false-negative (脚本通过/人工违规): %d" % false_neg,
        "  误报率 FP/(FP+TN): %s" % _ratio(false_pos, false_pos + true_neg),
        "  漏报率 FN/(FN+TP): %s" % _ratio(false_neg, false_neg + true_pos),
        "",
        "total: %d commits, %d consistent, %d mismatch"
        % (total, total - len(mismatches), len(mismatches)),
    ]
    report = "\n".join(lines)
    print(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return EXIT_VIOLATION if mismatches else EXIT_OK


def _ratio(part, whole):
    if whole == 0:
        return "n/a"
    return "%.1f%%" % (100.0 * part / whole)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="commitlint", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", default="rules.json",
                        help="规则配置文件路径 (默认 rules.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="检查单个提交")
    check.add_argument("--git", metavar="REF", help="从 git 仓库读取该提交")
    check.add_argument("--message", help="直接给出提交信息")
    check.add_argument("--message-file", help="从文件读取提交信息")
    check.add_argument("--files", nargs="*", help="改动文件列表")
    check.add_argument("--author-name", default="")
    check.add_argument("--author-email", default="")
    check.add_argument("--ref", default="", help="报告里显示的提交标识")
    check.set_defaults(func=cmd_check)

    evaluate = sub.add_parser("evaluate", help="与人工标注数据集对拍")
    evaluate.add_argument("--dataset", required=True, help="标注数据 JSON")
    evaluate.add_argument("--report", help="将报告写入文件")
    evaluate.set_defaults(func=cmd_evaluate)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
