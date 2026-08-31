library(shiny)
library(ggplot2)
library(DT)
library(tidyverse)
library(xtable)
library(dplyr)
library(readxl)
library(DBI)
library(RPostgres)
library(pool)

source("R/db.R")

db_pool <- bb_db_pool()
onStop(function() pool::poolClose(db_pool))

function(input, output, session) {

  # Refresh read-only league views periodically. This replaces the previous
  # process-lifetime Google Sheets snapshots and will also allow finalized
  # rounds to appear without restarting the Shiny process.
  schedule_data <- reactive({
    invalidateLater(30000, session)
    bb_get_schedule(db_pool)
  })

  standings_data <- reactive({
    invalidateLater(30000, session)
    bb_get_standings(db_pool)
  })

  alltime_data <- reactive({
    invalidateLater(60000, session)
    bb_get_alltime(db_pool)
  })

  records_data <- reactive({
    invalidateLater(60000, session)
    bb_get_records(db_pool)
  })

  aces_data <- reactive({
    invalidateLater(60000, session)
    bb_get_aces(db_pool)
  })

  champs_data <- reactive({
    invalidateLater(60000, session)
    bb_get_champions(db_pool)
  })

  values <- function() {
    payout <- data.frame(
      Place = seq(1:5),
      Pay = 0
    )

    pot <- ifelse(
      input$scale == "No",
      input$homies * 4,
      input$homies * 5
    )

    if (input$homies >= 21) {
      payout$Pay[1] <- round(0.40 * pot, digits = 0)
      payout$Pay[2] <- round(0.25 * pot, digits = 0)
      payout$Pay[3] <- round(0.15 * pot, digits = 0)
      payout$Pay[4] <- round(0.12 * pot, digits = 0)
      payout$Pay[5] <- round(0.08 * pot, digits = 0)
    } else if (input$homies >= 16) {
      payout$Pay[1] <- round(0.40 * pot, digits = 0)
      payout$Pay[2] <- round(0.30 * pot, digits = 0)
      payout$Pay[3] <- round(0.20 * pot, digits = 0)
      payout$Pay[4] <- round(0.10 * pot, digits = 0)
    } else if (input$homies >= 11) {
      payout$Pay[1] <- round(0.50 * pot, digits = 0)
      payout$Pay[2] <- round(0.30 * pot, digits = 0)
      payout$Pay[3] <- round(0.20 * pot, digits = 0)
    } else if (input$homies >= 6) {
      payout$Pay[1] <- round(0.60 * pot, digits = 0)
      payout$Pay[2] <- round(0.40 * pot, digits = 0)
    } else {
      payout$Pay[1] <- pot
    }

    payout
  }

  output$schedule <- renderDT({
    datatable(schedule_data())
  })

  output$standings <- renderDT({
    datatable(standings_data())
  })

  output$paytable <- renderTable({
    data.frame(values())
  })

  output$alltime <- renderDT({
    datatable(alltime_data())
  })

  output$records <- renderDT({
    datatable(records_data())
  })

  output$aces <- renderDT({
    datatable(aces_data())
  })

  output$champs <- renderDT({
    datatable(champs_data())
  })

  # This preserves the current Results Finalizer v2.0 behavior: it is a
  # read-only preview using the current database standings. Transactional
  # finalization will be added only after database parity is verified.
  resdata <- reactive({
    if (is.null(input$results)) {
      return("Waiting for data.")
    }

    in_file <- input$results

    in_file2 <- read_excel(in_file$datapath, 1) %>%
      transmute(
        Name = name,
        Raw = round_total_score,
        position
      ) %>%
      filter(position != "DNF")

    homies <- as.integer(nrow(in_file2))

    places <- case_when(
      homies >= 21 ~ 5,
      homies >= 16 ~ 4,
      homies >= 11 ~ 3,
      homies >= 6 ~ 2,
      TRUE ~ 1
    )

    merge(
      x = in_file2,
      y = standings_data(),
      by = "Name",
      all.x = TRUE
    ) %>%
      transmute(
        Name,
        Raw,
        HCP = suppressWarnings(
          case_when(
            is.na(Handicap) ~ 0,
            Handicap == "E" ~ 0,
            TRUE ~ as.numeric(Handicap)
          )
        ),
        Adjusted = Raw - HCP,
        homies,
        places
      ) %>%
      arrange(Adjusted) %>%
      mutate(critscore = Adjusted[places]) %>%
      filter(Adjusted <= critscore) %>%
      transmute(
        Name,
        Handicap = HCP,
        Raw,
        Adjusted
      )
  })

  output$resultable <- renderTable({
    req(resdata())
    data.frame(resdata())
  })

  playoffdata <- reactive({
    if (is.null(input$pround1)) {
      return("Waiting for Round 1.")
    }
    if (is.null(input$pround2)) {
      return("Waiting for Round 2.")
    }

    pround1 <- input$pround1
    r1in_file <- read_excel(pround1$datapath, 1) %>%
      transmute(Name = name, Rd1 = round_total_score)

    pround2 <- input$pround2
    r2in_file <- read_excel(pround2$datapath, 1) %>%
      transmute(Name = name, Rd2 = round_total_score)

    totalin_file <- merge(
      x = r1in_file,
      y = r2in_file,
      by = "Name",
      all.y = TRUE
    )

    merge(
      x = totalin_file,
      y = standings_data(),
      by = "Name",
      all.x = TRUE
    ) %>%
      transmute(
        Name,
        rd1topar = Rd1 - 59,
        rd2topar = Rd2 - 55,
        rd1text = case_when(
          rd1topar < 0 ~ as.character(rd1topar),
          Rd1 == 59 ~ "E",
          TRUE ~ paste0("+", rd1topar)
        ),
        rd2text = case_when(
          rd2topar < 0 ~ as.character(rd2topar),
          Rd2 == 55 ~ "E",
          TRUE ~ paste0("+", rd2topar)
        ),
        Round1 = paste0(Rd1, " (", rd1text, ")"),
        Round2 = paste0(Rd2, " (", rd2text, ")"),
        HCPn = ifelse(is.na(Handicap), 0, as.integer(Handicap)),
        HCPt = case_when(
          HCPn <= 0 ~ as.character(HCPn),
          TRUE ~ paste0("+", HCPn)
        ),
        Adjusted = as.integer(Rd1 + Rd2 - HCPn - HCPn),
        tottopar = Adjusted - 114,
        tottext = case_when(
          tottopar < 0 ~ as.character(tottopar),
          Rd1 + Rd2 == 114 ~ "E",
          TRUE ~ paste0("+", tottopar)
        ),
        tottext2 = paste0(Adjusted, " (", tottext, ")")
      ) %>%
      arrange(Adjusted) %>%
      select(
        Name,
        Round1,
        Round2,
        HCP = HCPt,
        Final = tottext2
      ) %>%
      filter(row_number() <= 10)
  })

  output$playofftable <- renderTable({
    req(playoffdata())
    data.frame(playoffdata())
  })
}
