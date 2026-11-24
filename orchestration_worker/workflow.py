"""Deterministic GitHub delivery intent from authenticated engineering emails.

This parser accepts instructions, not arbitrary prose as authorization. Unsupported
or conflicting workflows fail preflight before spending a Codex run.
"""
import re


class WorkflowError(ValueError):
    pass


SECTIONS = {'GIT', 'GITHUB', 'GITHUB WORKFLOW', 'ROADMAP'}
FIELDS = ('PUBLISH', 'CREATE_PR', 'POST_COMMENT', 'ISSUE_NUMBER')


def instruction_text(text):
    """Exclude fenced examples, quoted replies and forwarded message history."""
    lines = []
    fence = None
    for line in text.replace('\r\n', '\n').splitlines():
        stripped = line.strip()
        if re.match(r'(?i)^(?:On .+ wrote:|-+\s*(?:Original Message|Forwarded message))', stripped):
            break
        marker = re.match(r'^(`{3,}|~{3,})', stripped)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            lines.append('')
            continue
        if fence or stripped.startswith('>'):
            lines.append('')
            continue
        lines.append(line)
    return '\n'.join(lines)


def parse(text):
    clean = instruction_text(text)
    def field(name):
        values = re.findall(r'^\s*'+name+r'\s*[:=]\s*([^\n]*)$', clean, re.M | re.I)
        if len(values) > 1:
            raise WorkflowError('Duplicate workflow field: '+name)
        return values[0].strip() if values else None

    kind = field('TASK_TYPE')
    explicit = {}
    for name in FIELDS:
        value = field(name)
        if value is None:
            continue
        if name == 'ISSUE_NUMBER':
            if not re.fullmatch(r'[1-9][0-9]{0,9}', value):
                raise WorkflowError('ISSUE_NUMBER must be a positive integer')
        else:
            value = value.upper()
            if value not in ('YES', 'NO'):
                raise WorkflowError(name+' must be YES or NO')
        explicit[name] = value
    inferred = set()
    denied = set()
    targets = set()
    recognized = False
    if kind is None or kind.upper() == 'ENGINEERING':
        section = None
        sections = []
        for line in clean.splitlines():
            heading = re.sub(r'^#{1,6}\s*', '', line.strip()).strip('*').rstrip(':').strip()
            if heading.upper() in SECTIONS:
                section = heading.upper()
                recognized = True
                continue
            imperative = re.match(r'^(?:DO NOT|NEVER|NO|PUSH|COMMIT|OPEN|CREATE|ADD|POST)\b', heading)
            if ((re.fullmatch(r'[A-Z][A-Z /_-]{2,}', heading) and not imperative)
                    or re.match(r'^#{1,6}\s', line.strip())):
                section = None
            if section:
                line = re.sub(r'^\s*(?:\d+[.)]|[-*•])\s+', '', line)
                sections.append(line)

        if kind is None:
            # Ordinary task emails need no control headings or TASK_TYPE field.
            sections = [re.sub(r'^\s*(?:\d+[.)]|[-*•])\s+', '', line)
                        for line in clean.splitlines()]
        # Sentence punctuation, semicolons, newlines and list markers are layout,
        # not authorization. Keep comma clauses together to preserve negations.
        # Mail clients wrap comma-separated instructions across physical lines.
        # Keep the whole instruction, including any negation/condition, together.
        instructions=re.sub(r',[ \t]*\n[ \t]*', ', ', '\n'.join(sections))
        clauses = re.split(r'[;\n]+|[.!?](?=\s|$)', instructions)
        for clause in clauses:
            clause = clause.strip()
            actions = set()
            if re.search(r'\bpush\b', clause, re.I):
                actions.add('PUBLISH')
            if re.search(r'\b(?:create|open)\s+(?:a\s+)?(?:PR|pull request)\b', clause, re.I):
                actions.add('CREATE_PR')
            if re.search(r'\b(?:add|post)\b.*\bcomment\b', clause, re.I):
                actions.add('POST_COMMENT')
            if re.search(r"\b(?:not|never|without|no|don't|do not)\b", clause, re.I):
                denied.update(actions)
                continue
            if re.search(r'\b(?:if|unless|until|provided|pending|after approval|once approved)\b', clause, re.I):
                continue
            if not re.match(r'^(?:please\s+)?(?:work|commit|push|create|open|add|post)\b', clause, re.I):
                continue
            inferred.update(actions)
            if 'POST_COMMENT' in actions:
                targets.update(re.findall(r'\b(?:GitHub\s+)?issue\s*#?([1-9][0-9]*)\b', clause, re.I))
        if len(targets) > 1:
            raise WorkflowError('Ambiguous workflow: multiple issue targets')
        if targets and explicit.get('ISSUE_NUMBER') not in (None, next(iter(targets))):
            raise WorkflowError('Conflicting issue targets')

    # Asking for a PR includes publishing its branch, unless explicitly denied.
    if ('CREATE_PR' in inferred and 'CREATE_PR' not in denied
            and explicit.get('PUBLISH') != 'NO' and 'PUBLISH' not in denied
            and not re.search(r'\bpush\b', clean, re.I)):
        inferred.add('PUBLISH')

    values = {name: 'YES' for name in inferred - denied}
    if targets:
        values['ISSUE_NUMBER'] = next(iter(targets))
    # Explicit NO always overrides inferred intent. A contradictory explicit YES
    # is rejected rather than silently overriding a natural-language prohibition.
    for name in denied:
        if explicit.get(name) == 'YES':
            raise WorkflowError('Conflicting authorization: '+name)
    values.update(explicit)
    return values, recognized, field('PR_REQUIRED')


def normalize(text):
    values, _, _ = parse(text)
    # Canonical fields are consumed by the existing supervisor and agent prompt.
    text = text.replace('\r\n', '\n')
    clean = instruction_text(text)
    for name in FIELDS:
        if name in values:
            clean = re.sub(r'^\s*'+name+r'\s*[:=]\s*[^\n]*$', '', clean, flags=re.M | re.I)
    # Keep quoted context out of control-field matching, as well as inference.
    return '\n'.join([name+': '+values[name] for name in FIELDS if name in values]+[clean.lstrip('\n')])


def plan(text):
    values, recognized, required = parse(text)
    result = {name.lower(): values.get(name) == 'YES' for name in FIELDS[:-1]}
    result['issue_number'] = int(values['ISSUE_NUMBER']) if 'ISSUE_NUMBER' in values else None
    if result['create_pr'] and not result['publish']:
        raise WorkflowError('PR requested without push authorization; specify both or remove the PR request')
    if result['post_comment'] and result['issue_number'] is None:
        raise WorkflowError('Issue comment requested without a single issue number')
    if required and required.lower() == 'true' and not result['create_pr']:
        raise WorkflowError('PR_REQUIRED=true but no unconditional PR instruction recognized; use CREATE_PR: YES and PUBLISH: YES')
    if recognized and not any(result[key] for key in ('publish', 'create_pr', 'post_comment')):
        # Explicit denials are a legitimate local-only request.
        if not any(values.get(key) == 'NO' for key in FIELDS[:-1]):
            raise WorkflowError('GitHub workflow has no recognized unconditional delivery instruction; use explicit YES/NO fields')
    return result
