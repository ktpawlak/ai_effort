# Linux 6.2/6.3 backport

The source change alone is not safe with the Linux 6.2 or 6.3
`kselftest_harness.h`.

In those versions, `SKIP(return, ...)` in `FIXTURE_SETUP()` marks the case as
skipped and returns from setup, but the fixture wrapper still enters the test
body. Backport upstream commit `372b304c1e51` first:

```diff
-			if (!_metadata->passed) \
+			if (!_metadata->passed || _metadata->skip) \
				return; \
```

Then change `so_incoming_cpu.c`:

```diff
 FIXTURE_SETUP(so_incoming_cpu)
 {
 	self->nproc = get_nprocs();
-	ASSERT_LE(2, self->nproc);
+	if (self->nproc < 2)
+		SKIP(return, "requires at least two CPUs");
```

An alternative requiring no kernel-source compatibility change is to have the
external test runner detect `nproc < 2` and return `KSFT_SKIP` without invoking
`so_incoming_cpu`.

