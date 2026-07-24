; Romulan demo program in 6502 assembly.
; Builds the same 32 KB ROM image as the annotated hex dump ram_write.s.

        .org $8000

reset:  CLC             ; clear carry
        LDA #$05        ; A = $05
        STA $4000       ; write $05 to $4000
        ADC #$0F        ; A = $05 + $0F = $14
        STA $4000       ; write $14 to $4000
        LDA #$08        ; A = $08
        STA $4010       ; write $08 to $4010
        STP             ; stop the CPU (ends capture)

        .org $FFFC
        .word reset     ; reset vector -> $8000
        .word reset     ; IRQ/BRK vector -> $8000
