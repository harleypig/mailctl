# Error codes

Every refusal mailctl makes carries a `code`: a short name for the
condition, so a script or an API client can tell one refusal from another
without reading the message. This page lists every code. From v1.0.0 the
codes are part of the promise in [RELEASING.md][promise]: a code is never
renamed, removed, or reused, and new ones may be added. Treat a code you
do not know as an ordinary failure.

## Where a code appears

* **The command line, under `--json`.** A failure ends with one JSON line,
  the last on stderr: `{"version", "error": {"message", "code",
  "fields"}}`. `fields` holds the values the refusal carries, as the API's
  does, and is left out where it carries none. Exit status 1 is a failure
  or refusal. Exit status 2 is a command line
  rejected before anything ran: the usage and the error come first, as
  text, and the coded line follows them. See [Output for scripts][out] in
  the README.
* **The API.** A refusal is answered with JSON-RPC error `1`, and its
  `data` is the same document, `fields` always present: the values a
  client needs, such as a setting's name. See [Errors][api-errors] in the
  README.

Two failures carry no code: an interrupted run (exit status 130, and
`interrupted` under `--json`), and an unexpected failure, which is named
by its type alone (exit status 1, or `-32603` on the API). A cancelled API
request is `-32800`, with no code.

## The command line's own

Exit status 2 where the command line is rejected before anything runs,
and 1 where one of the command's own checks refuses it after parsing.

| Code | Means |
|------|-------|
| `invalid_arguments` | the arguments alone are wrong: an unknown command or option, two that cannot be combined, one that needs another, or a value that cannot be read. Also `mailctl-api`'s own arguments |
| `gone_command` | a command named by its name from before the commands were grouped |
| `gone_option` | an option named by its old name, such as `--fileinto` |
| `confirmation_needed` | a write needs confirming and there is no terminal to ask; give `--yes` or `--dry-run` |
| `filter_file_unreadable` | the `--filter` file cannot be read |
| `filter_file_not_utf8` | the `--filter` file is not UTF-8 |
| `nothing_to_derive` | the `--like` message has none of the headers asked for |

A bad `--imap-port`, `--sieve-port`, or `--sieve-tls` is
`setting_invalid`, and `--no-subscribe` without `--create-folder` is
`subscribe_needs_create`, both at exit status 2.

## The API's own

| Code | Means |
|------|-------|
| `not_initialized` | a method was called before `initialize` succeeded |
| `already_initialized` | `initialize` was called a second time |
| `plan_unknown` | no plan with that id is waiting: it was used, discarded, or dropped as one of more than 32 waiting; `fields.plan_id` names it |
| `password_declined` | the client declined `credentials.prompt`, its answer held no password, or its input ended |
| `prompt_input_full` | the client sent 64 messages not yet served while `credentials.prompt` waited, so its answer could not be read; `fields.limit` names the limit |

## Settings and credentials

| Code | Means |
|------|-------|
| `missing_settings` | a required setting, such as the host or the user, is not set |
| `setting_invalid` | a setting's value is not one it takes, or a provider specific is malformed; `fields.setting` names it |
| `setting_not_read` | a setting the provider has no use for was given; `fields.setting` names it |
| `unknown_provider` | the `provider` setting names no provider |
| `config_invalid` | `config.toml` is not valid TOML |
| `config_unreadable` | `config.toml` cannot be read |
| `env_file_invalid` | the env file has a line that is not `KEY=VALUE`; the line is named by number only |
| `env_file_not_utf8` | the env file is not UTF-8 |
| `env_file_unreadable` | the env file cannot be read |
| `env_file_mode` | the env file sets `MAILCTL_PASSWORD` and others can read it |
| `password_file_mode` | the password file is readable by group or other |
| `password_file_unreadable` | the password file cannot be read |
| `password_file_not_utf8` | the password file is not UTF-8 |
| `password_file_empty` | the password file is empty |
| `password_command_empty` | the password command is empty |
| `password_command_failed` | the password command could not run, or exited non-zero |
| `password_command_no_output` | the password command printed nothing |
| `no_password` | no source gives a password and nothing can ask for one, or the password is empty |
| `credentials_not_ascii` | the user or password holds a character IMAP's login cannot send; nothing was sent |
| `backup_outside_root` | a backup would go outside `MAILCTL_BACKUP_ROOT` |

## Criteria and the filter document

| Code | Means |
|------|-------|
| `no_criteria` | no criteria were given |
| `header_name_empty` | a criterion has no header name |
| `criterion_empty` | a criterion has an empty value |
| `match_invalid` | the match mode is not one there is |
| `compare_invalid` | the comparison is not one there is |
| `body_compare` | a body criterion was combined with a comparison other than a substring test |
| `date_invalid` | a date is not a real `YYYY-MM-DD` |
| `age_invalid` | an age is not a whole number of days or weeks, at least one day |
| `date_range_empty` | the since date is not before the before date |
| `state_in_rule` | a saved filter was asked to test a date, or read or flagged state |
| `criteria_and_raw` | both criteria and a raw query were given |
| `raw_non_ascii` | a raw query holds a character outside ASCII |
| `search_value_invalid` | a search value is not valid text |
| `filter_invalid` | a filter document is not valid JSON, or not the right shape |
| `filter_version_unsupported` | a filter document's version is one this mailctl does not read |
| `limit_below_one` | the message limit is below 1 |
| `unknown_sort_key` | the sort key is not one there is |

## Messages and UIDs

| Code | Means |
|------|-------|
| `uid_below_one` | a UID is below 1 |
| `no_such_message` | the folder holds no message with that UID; `fields` name the folder and the UIDs |
| `no_uids` | `mail mark` was given no UID |
| `uidvalidity_out_of_range` | a UIDVALIDITY is not a number the server could give |
| `uidvalidity_changed` | the folder has been renumbered since the UIDs were read |
| `no_uidvalidity` | the server reports no UIDVALIDITY, so a pinned UID cannot be checked |
| `no_marks` | `mail mark` has no flag to set or clear |
| `flag_set_and_cleared` | the same flag was asked to be set and cleared |
| `keyword_invalid` | a keyword is not one word of printable ASCII |
| `keyword_system_flag` | a system flag was given as a keyword |
| `no_mail_action` | the actions leave delivered mail as it is, so there is nothing to apply |
| `max_messages` | more messages match than the `--max-messages` ceiling; nothing was changed |
| `senders_ceiling` | `mail senders` found more messages than its ceiling; no header was read |
| `at_least_one` | `mail senders`' `--top`, `--min`, or `--max-messages` is below 1 |
| `unknown_grouping` | `mail senders` cannot group by that |

## Folders

| Code | Means |
|------|-------|
| `no_such_folder` | no folder by that name on the server |
| `folder_missing` | the target folder does not exist and was not asked to be created |
| `folder_exists` | the new name of a rename already exists |
| `folder_case_variant` | the name differs only in case from a folder that exists |
| `folder_name_empty` | a folder name is empty |
| `folder_not_planned` | a folder was asked to be created that the plan does not create |
| `needs_mail` | the folder can be created only over the mail connection, which the command was not given |
| `subscribe_needs_create` | leaving a folder unsubscribed applies only to a folder the change creates |
| `rename_inbox` | INBOX cannot be renamed |
| `rename_same_name` | the folder already has that name |
| `rename_into_itself` | a folder cannot be moved inside itself |
| `rename_subfolder_clash` | a subfolder would be moved onto a folder that exists |
| `rename_not_started` | the rename failed before anything changed |
| `rename_not_activated` | the folder was renamed and the filter set stored, but it could not be activated |
| `rename_interrupted` | the folder was renamed, but filters still file into the old name |

## Filters and filter sets

| Code | Means |
|------|-------|
| `no_action` | no action was given |
| `no_such_filter` | no filter by that name in the filter set |
| `unknown_anchor` | the filter named for `--before` or `--after` is not in the filter set |
| `self_anchor` | a filter was placed relative to itself |
| `rule_exists` | a filter by that name already exists |
| `rule_name_taken` | a rename's new name is already a filter's |
| `filter_name_empty` | a filter's new name is empty |
| `filter_name_invalid` | a name that cannot be written as a filter name and read back the same |
| `filter_unnamed` | the filter has no name written in the filter set to change |
| `filter_cannot_disable` | the filter's shape does not let it be switched off |
| `filter_cannot_enable` | the filter's shape does not let it be switched back on |
| `replace_disabled` | a disabled filter cannot be replaced with a test of more than one line |
| `filters_not_mergeable` | filters asked to be merged do not test and act alike |
| `rearrangement_repeats` | a rearrangement names a filter more than once |
| `kept_test_unreadable` | a disabled filter's kept test is not one mailctl can read |
| `folder_rewrite_failed` | a folder in a filter could not be rewritten in place |
| `parser_mismatch` | mailctl and its Sieve library disagree on the filter set's layout |
| `optimize_defect` | the proposed arrangement does not read back as proposed, so nothing is offered |
| `unknown_kind` | `filter optimize` was asked to skip a kind of change there is not |
| `filterset_unparseable` | the filter set cannot be parsed, so nothing is merged into it |
| `filterset_unnamed` | no filter set is active, and none was named |
| `filterset_empty` | the filter set is empty |
| `no_active_script` | no filter set is active on the server |
| `restore_needs_script` | no filter set is active to restore over, and none was named |
| `filterset_changed` | the filter set changed, or another was made active, since the plan |
| `nothing_to_change` | the plan changes nothing, so there is nothing to carry out |
| `extension_disabled` | the filter needs an extension `disabled_extensions` turns off |
| `extension_missing` | the filter needs an extension the server does not advertise |
| `forbidden_action` | the host forbids the action, as MXroute does `redirect` |
| `unimplemented_action` | mailctl does not write the action (`notify`, `vacation`) |
| `unsupported` | the provider declares it cannot do or express this |
| `server_capability_missing` | the server does not advertise what the request needs; `fields.capability` names it |

## Backups, baselines, and the migration

| Code | Means |
|------|-------|
| `backup_exists` | the backup file named already exists, and a backup never replaces a file |
| `backup_unreadable` | the backup to restore cannot be read, or is not UTF-8 |
| `empty_backup` | the backup to restore is empty |
| `credential_file` | the file to restore is one this run reads the password from |
| `file_unwritable` | a backup or baseline could not be written |
| `no_baseline` | no baseline has been saved for this host |
| `no_host` | no host is configured, so there is no baseline to name |
| `baseline_host_invalid` | the host cannot name a baseline file |
| `baseline_other_provider` | the baseline was taken through another provider |
| `baseline_read_failed` | the baseline file could not be read |
| `baseline_unreadable` | the baseline file is not one mailctl can read; save a new one |
| `baseline_changed` | the baseline file changed after the plan; nothing was written |
| `probe_invalid` | a document given as a probe is not one |
| `migration_conflict` | moving the old config directory would overwrite something |
| `migration_changed` | a directory changed after the migration was planned; nothing was moved |
| `migration_appeared` | a destination appeared during the move; it was left alone |
| `migration_failed` | the filesystem refused the move |
| `migration_reference_unrewritable` | a `config.toml` path reaches the old directory only through a variable |

## Talking to the servers

| Code | Means |
|------|-------|
| `connection_failed` | the server cannot be reached, or the protocol failed while connecting |
| `tls_failed` | TLS failed |
| `starttls_not_offered` | the ManageSieve server does not offer STARTTLS, which the TLS mode asked for; nothing was sent |
| `auth_failed` | the server refused the login |
| `noop_failed` | the mail connection's liveness check failed |
| `server_identity_failed` | the connection was lost reading the mail server's identity |
| `folder_list_failed` | listing folders failed |
| `folder_counts_failed` | counting the folders' messages failed |
| `folder_counts_unreadable` | the counts the server sent could not be read |
| `folder_create_failed` | a folder could not be created |
| `folder_rename_failed` | a folder could not be renamed |
| `folder_unopenable` | a folder could not be opened |
| `folder_select_failed` | the connection was lost opening a folder |
| `subscription_failed` | subscribing or unsubscribing failed |
| `subscribe_unconfirmed` | the server accepted a subscribe but does not list the folder as subscribed |
| `message_count_failed` | a folder's message count could not be read |
| `search_failed` | a search or sort failed |
| `raw_query_failed` | a raw query failed |
| `fetch_failed` | reading messages failed |
| `flags_failed` | setting or clearing flags failed |
| `move_failed` | moving messages failed |
| `copy_failed` | copying messages failed |
| `delete_failed` | deleting messages failed |
| `partial_execution` | a pass over existing mail stopped part-way |
| `filterset_list_failed` | listing the filter sets failed |
| `filterset_read_failed` | reading a filter set failed |
| `filterset_not_utf8` | the filter set is not UTF-8 |
| `filterset_rejected` | the server's check refused the filter set; nothing was uploaded |
| `filterset_check_failed` | the connection was lost while the server checked the filter set; nothing was uploaded |
| `filterset_upload_failed` | storing a filter set failed |
| `filterset_activate_failed` | activating a filter set failed |
| `internal_error` | a guard on mailctl's own use of itself; only a bug reaches it |

`connection_failed` and the ManageSieve half's `tls_failed` and
`starttls_not_offered` carry the provider's advice in `fields.advice`:
`check_settings`, with the settings to check in `fields.settings`, or
`try_setting`, with the other value of `fields.setting` to try in
`fields.value`. The command line also turns the advice into the flags to
check, in its message.

[promise]: ../RELEASING.md#what-v1-promises
[out]: ../README.md#output-for-scripts
[api-errors]: ../README.md#errors
