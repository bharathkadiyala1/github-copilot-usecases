      *> TXNSUM.cbl - daily account summary (batch job TXNSUM01)
      *> REFERENCE ONLY: shown to Copilot for analysis. It is not compiled or run here.
      *> Note: COMPUTE without ROUNDED TRUNCATES. This is why COBOL results differ.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TXNSUM.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT TXN-FILE ASSIGN TO TXNIN ORGANIZATION LINE SEQUENTIAL.
           SELECT SUM-FILE ASSIGN TO SUMOUT ORGANIZATION LINE SEQUENTIAL.
           SELECT REJ-FILE ASSIGN TO REJOUT ORGANIZATION LINE SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  TXN-FILE.
           COPY TXNREC.
       FD  SUM-FILE.
       01  SUM-LINE               PIC X(120).
       FD  REJ-FILE.
       01  REJ-LINE               PIC X(40).
       WORKING-STORAGE SECTION.
       01  WS-EOF                 PIC X VALUE 'N'.
       01  WS-LINE-NO             PIC 9(9) VALUE 0.
       01  WS-REASON              PIC X(12).
       01  WS-CENTS               PIC 9(11).
       01  WS-CAD                 PIC 9(11).
       01  WS-FEE                 PIC 9(7).
       01  WS-ACCT-COUNT          PIC 9(5) VALUE 0.
       01  WS-SEEN-COUNT          PIC 9(7) VALUE 0.
       01  WS-SEEN-TABLE.
           05  WS-SEEN-ID         PIC X(8) OCCURS 100000 TIMES
                                  INDEXED BY SEEN-IX.
       01  WS-ACCT-TABLE.
           05  WS-ACCT OCCURS 5000 TIMES INDEXED BY ACCT-IX.
               10  A-ID           PIC X(10).
               10  A-COUNT        PIC 9(7).
               10  A-CREDITS      PIC 9(11).
               10  A-DEBITS       PIC 9(11).
               10  A-FEES         PIC 9(9).
               10  A-LAST         PIC X(8).
               10  A-MONTHEND     PIC 9(7).
               10  A-ATM          PIC 9(7).
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT TXN-FILE OUTPUT SUM-FILE REJ-FILE
           PERFORM UNTIL WS-EOF = 'Y'
               READ TXN-FILE AT END MOVE 'Y' TO WS-EOF
                   NOT AT END
                       ADD 1 TO WS-LINE-NO
                       PERFORM VALIDATE-PARA
                       IF WS-REASON = SPACES
                           PERFORM POST-PARA
                       ELSE
                           PERFORM REJECT-PARA
                       END-IF
               END-READ
           END-PERFORM
           PERFORM WRITE-SUMMARY-PARA
           CLOSE TXN-FILE SUM-FILE REJ-FILE
           STOP RUN.
       VALIDATE-PARA.
           MOVE SPACES TO WS-REASON
           IF TXN-ID = SPACES MOVE 'MISSING_ID' TO WS-REASON END-IF
           IF WS-REASON = SPACES AND TXN-ACCOUNT = SPACES
               MOVE 'BAD_ACCOUNT' TO WS-REASON END-IF
           IF WS-REASON = SPACES AND TXN-DATE NOT NUMERIC
               MOVE 'BAD_DATE' TO WS-REASON END-IF
      *>   ...calendar check with FUNCTION TEST-DATE-YYYYMMDD omitted for brevity...
           IF WS-REASON = SPACES
               EVALUATE TXN-TYPE
                   WHEN 'DEP' WHEN 'INT' WHEN 'XFRIN'
                   WHEN 'WDR' WHEN 'WIRE' WHEN 'POS' WHEN 'XFROUT'
                       CONTINUE
                   WHEN OTHER MOVE 'BAD_TYPE' TO WS-REASON
               END-EVALUATE
           END-IF
           IF WS-REASON = SPACES
               COMPUTE WS-CENTS = FUNCTION NUMVAL(TXN-AMOUNT-TXT) * 100
               IF WS-CENTS <= 0 MOVE 'BAD_AMOUNT' TO WS-REASON END-IF
           END-IF
           IF WS-REASON = SPACES AND TXN-CURRENCY NOT = 'CAD'
                                 AND TXN-CURRENCY NOT = 'USD'
               MOVE 'BAD_CURRENCY' TO WS-REASON END-IF
           IF WS-REASON = SPACES
               SET SEEN-IX TO 1
               SEARCH WS-SEEN-ID
                   AT END
                       ADD 1 TO WS-SEEN-COUNT
                       MOVE TXN-ID TO WS-SEEN-ID (WS-SEEN-COUNT)
                   WHEN WS-SEEN-ID (SEEN-IX) = TXN-ID
                       MOVE 'DUPLICATE' TO WS-REASON
               END-SEARCH
           END-IF.
       POST-PARA.
           IF TXN-CURRENCY = 'USD'
               COMPUTE WS-CAD = WS-CENTS * 1.35
           ELSE
               MOVE WS-CENTS TO WS-CAD
           END-IF
           MOVE 0 TO WS-FEE
           IF TXN-TYPE = 'WDR' AND TXN-CHANNEL = 'ATM' AND WS-CAD > 50000
               MOVE 200 TO WS-FEE
           END-IF
           IF TXN-TYPE = 'WIRE'
               COMPUTE WS-FEE = WS-CAD / 1000
               IF WS-FEE < 500 MOVE 500 TO WS-FEE END-IF
               IF WS-FEE > 2500 MOVE 2500 TO WS-FEE END-IF
           END-IF
      *>   ...find or add the account in WS-ACCT-TABLE and add the amounts...
           CONTINUE.
       REJECT-PARA.
           STRING WS-LINE-NO ',' TXN-ID ',' WS-REASON
               DELIMITED BY SIZE INTO REJ-LINE
           WRITE REJ-LINE.
       WRITE-SUMMARY-PARA.
      *>   ...sort WS-ACCT-TABLE by A-ID and write one SUM-LINE per account...
           CONTINUE.
