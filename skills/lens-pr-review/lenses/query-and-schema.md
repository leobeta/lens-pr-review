---
id: query-and-schema
summary: Do queries, joins and columns do what the data model says they should?
may_block: if-verified
---

# Lens: query-and-schema

## When to apply
A PR adds or changes a query, join, filter, column, DB access helper or data cleanup.

## What to do before commenting
1. Derive what SQL the code produces. If a skill for the query builder exists in the repository, use it; otherwise read the builder calls carefully.
2. Find out whether the joined columns can be NULL (schema, generated model, or the code that writes them). An inner join on a nullable column drops rows; a left join on a NOT NULL one is noise.
3. Check whether a column already on the table makes a join unnecessary.
4. Check whether a filter repeats what a sibling query already guarantees, or whether a loop issues one query per row.
5. For deletes and updates, think about foreign keys and ordering: what happens when a referenced row still exists?
6. If you cannot see the column definition, say so and ask instead of guessing.

## How to phrase it
Say what you traced ('the column is NOT NULL, so...'). Ask when you could not verify the schema. Mark a comment as blocking only when you verified a concrete wrong result.

## Example comments (synthetic, for tone only)
- `joinDown` instead of `leftJoinDown` should be enough here since the foreign key is NOT NULL, right?
- `Order` already has the account id column, so we may not need to join `Account` just for this.
- `getList()` handles the connection for us, so I think we can drop the manual handling.

## Not a finding when
The join type is required because the column really is nullable and the rows must be kept.
