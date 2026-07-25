/// The always-on-top "caught" pop (CLAUDE.md section 3, receive path).
///
/// Android counterpart of daemon/receive/toast.py: shows the caught item's
/// name, a thumbnail when it's an image, and opens the item when tapped.
/// Auto-dismisses after a few seconds.
library;

import 'package:flutter/material.dart';

import 'dispatch.dart';

const Duration popDuration = Duration(seconds: 5);

/// Show the pop for a freshly caught payload. [onOpen] fires if it's tapped.
void showPop(
  BuildContext context,
  Caught caught, {
  required VoidCallback onOpen,
}) {
  ScaffoldMessenger.of(context)
    ..clearSnackBars()
    ..showSnackBar(
      SnackBar(
        duration: popDuration,
        backgroundColor: const Color(0xFF222222),
        behavior: SnackBarBehavior.floating,
        content: _PopBody(caught: caught, onOpen: onOpen),
        // The whole body is tappable, but an explicit action makes it obvious
        // that this pop does something.
        action: SnackBarAction(
          label: 'OPEN',
          textColor: const Color(0xFF8AB4F8),
          onPressed: onOpen,
        ),
      ),
    );
}

class _PopBody extends StatelessWidget {
  const _PopBody({required this.caught, required this.onOpen});

  final Caught caught;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onOpen,
      child: Row(
        children: [
          if (caught.isImage)
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(4),
                // Best effort: a pop without its picture still beats no pop.
                child: Image.file(
                  caught.file!,
                  width: 56,
                  height: 56,
                  fit: BoxFit.cover,
                  errorBuilder: (_, _, _) => const SizedBox.shrink(),
                ),
              ),
            ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  'caught: ${caught.label}',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white),
                ),
                Text(
                  'from ${caught.sender} — tap to open',
                  style: const TextStyle(color: Colors.white54, fontSize: 11),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
