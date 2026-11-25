      *> TXNREC.cpy - record layout of the daily transaction file (72 bytes)
      *> REFERENCE ONLY: this file is read by Copilot and people. It is not executed here.
       01  TXN-RECORD.
           05  TXN-ID            PIC X(8).
           05  TXN-ACCOUNT       PIC X(10).
           05  TXN-DATE          PIC X(8).
           05  TXN-TYPE          PIC X(6).
           05  TXN-AMOUNT-TXT    PIC X(12).
           05  TXN-CURRENCY      PIC X(3).
           05  TXN-CHANNEL       PIC X(6).
           05  TXN-MEMO          PIC X(19).
