library(shiny)
library(ggplot2)
library(DT)
library(googlesheets4)
library(tidyverse)
library(xtable)
library(dplyr)
library(readxl)
gs4_deauth()

link = 'https://docs.google.com/spreadsheets/d/1_NvpAOZSjCd-hvwM_3MCx6DKh8aHnvdPXY-3zuKeaV0/edit?gid=1153932697'

schedule <- read_sheet(link,
                      sheet = "League Schedule",
                      range = 'A:H') %>%
  data.frame() %>%
  mutate(Daten = as.Date(Datend)) %>%
  filter(Daten >= (Sys.Date()-1)) %>%
  select(-Daten,-Datend)

standings <- read_sheet(link,
                        sheet = "Leaderboard") %>%
  data.frame() %>%
  arrange(desc(Points))

alltime <- read_sheet(link,
                      sheet = "Current All Time") %>%
  data.frame() %>%
  select(-milestonen)

records <- read_sheet(link,
                      sheet = "Course Records") %>%
  data.frame() %>%
  mutate(Date = as.Date(Date)) %>%
  arrange(Course,Layout,desc(Date))

aces <- read_sheet(link,
                   sheet = "Aces") %>%
  data.frame() %>%
  mutate(Date = as.Date(Date)) %>%
  arrange(desc(Date))

# semis <- read_sheet("https://docs.google.com/spreadsheets/d/1Kbaa2c3rCCoG0eShL1ktklP-MLSliiDWCfnILd3tYn4/edit?gid=1858177861",
#                     sheet = "Semifinals") %>%
#   data.frame() %>%
#   arrange(Place)

champs <- read_sheet(link,
                    sheet = "Hall of Champions") %>%
  data.frame()

function(input, output) {
  
  values <- function(){
    payout <- data.frame(Place=seq(1:5),
                         Pay = 0)
    pot = ifelse(input$scale == "No", input$homies*4, input$homies*5)
    
    if(input$homies >= 21) {
      payout$Pay[1] = round((0.4*pot),digits=0)
      payout$Pay[2] = round((0.25*pot),digits=0)
      payout$Pay[3] = round((0.15*pot),digits=0)
      payout$Pay[4] = round((0.12*pot),digits=0)
      payout$Pay[5] = round((0.08*pot),digits=0)
    } else if(input$homies >= 16) {
      payout$Pay[1] = round((0.4*pot),digits=0)
      payout$Pay[2] = round((0.3*pot),digits=0)
      payout$Pay[3] = round((0.2*pot),digits=0)
      payout$Pay[4] = round((0.1*pot),digits=0)
    } else if(input$homies >= 11) {
      payout$Pay[1] = round((0.5*pot),digits=0)
      payout$Pay[2] = round((0.3*pot),digits=0)
      payout$Pay[3] = round((0.2*pot),digits=0)
    } else if(input$homies >= 6) {
      payout$Pay[1] = round((0.6*pot),digits=0)
      payout$Pay[2] = round((0.4*pot),digits=0)
    } else {
      payout$Pay[1] = pot
    }
    return(payout)
    }

  
  output$schedule <- renderDT({
    schedule %>%
      datatable()  })
  
  output$standings <- renderDT({
    standings %>%
      datatable()  })
  
  output$paytable <- renderTable({
    payout <- values() %>%
      data.frame() })
  
  output$alltime <- renderDT({
    alltime %>%
      datatable() })
  
  output$records <- renderDT({
    records %>%
      datatable() })
  
  output$aces <- renderDT({
    aces %>%
      datatable() })
  
  # output$semis <- renderDT({
  #   semis %>%
  #     datatable()})
  
  output$champs <- renderDT({
    champs %>%
      datatable()}) 
  
  resdata <- reactive({
    if (is.null(input$results)) {
      return("Waiting for data.")
    }
    # actually read the file
    inFile = input$results
    inFile2 = read_excel(inFile$datapath, 1) %>%
      transmute(Name=name,
                Raw=round_total_score,
                position) %>%
      filter(position != 'DNF')
    homies = as.integer(nrow(inFile2))
    places = case_when(homies >= 21 ~ 5,
                       homies >= 16 ~ 4,
                       homies >= 11 ~ 3,
                       homies >= 6 ~ 2,
                       TRUE ~ 1)
    merge(x=inFile2,y=standings,by="Name",all.x=TRUE) %>%
      transmute(Name,
                Raw,
                HCP = suppressWarnings(case_when(is.na(Handicap) ~ 0,
                                Handicap == 'E' ~ 0,
                                TRUE ~ as.numeric(Handicap))),
                Adjusted = Raw - HCP,
                homies,
                places) %>%
      arrange(Adjusted) %>%
      mutate(critscore = Adjusted[places]) %>%
      filter(Adjusted <= critscore) %>%
      transmute(Name,
                Handicap = HCP,
                Raw,
                Adjusted)
  })
  
  output$resultable <- renderTable({
    # render only if there is data available
    req(resdata())
    
    # reactives are only callable inside an reactive context like render
    data <- resdata() %>% 
      data.frame()
    })

  playoffdata <- reactive({
    if (is.null(input$pround1)) {
      return("Waiting for Round 1.")
    }
    if(is.null(input$pround2)) {
      return("Waiting for Round 2.")
    }
    # actually read the files
    pround1 = input$pround1
    r1inFile = read_excel(pround1$datapath, 1) %>%
      transmute(Name=name,
                Rd1=round_total_score)
    pround2 = input$pround2
    r2inFile = read_excel(pround2$datapath, 1) %>%
      transmute(Name=name,
                Rd2=round_total_score)
    totalinFile = merge(x=r1inFile,y=r2inFile,by="Name",all.y=TRUE) #eliminate DNFs
    
    merge(x=totalinFile,y=standings,by="Name",all.x=TRUE) %>%
      transmute(Name,
                rd1topar = Rd1 - 59,
                rd2topar = Rd2 - 55,
                rd1text = case_when(rd1topar < 0 ~ as.character(rd1topar),
                                    Rd1 == 59 ~ 'E',
                                    TRUE ~ paste0("+",rd1topar)),
                rd2text = case_when(rd2topar < 0 ~ as.character(rd2topar),
                                    Rd2 == 55 ~ 'E',
                                    TRUE ~ paste0("+",rd2topar)),
                Round1 = paste0(Rd1, " (", rd1text, ")"),
                Round2 = paste0(Rd2, " (", rd2text, ")"),
                HCPn = ifelse(is.na(Handicap),0,as.integer(Handicap)),
                HCPt = case_when(HCPn <= 0 ~ as.character(HCPn),
                                 TRUE ~ paste0('+',HCPn)),
                Adjusted = as.integer(Rd1 + Rd2 - HCPn - HCPn),
                tottopar = Adjusted - 114,
                tottext = case_when(tottopar < 0 ~ as.character(tottopar),
                                    Rd1 + Rd2 == 114 ~ 'E',
                                    TRUE ~ paste0("+",tottopar)),
                tottext2 = paste0(Adjusted, " (",tottext, ")")) %>%
      arrange(Adjusted) %>%
      select(Name,
             Round1,
             Round2,
             HCP=HCPt,
             Final=tottext2) %>%
      filter(row_number() <= 10)
  })
  
  output$playofftable <- renderTable({
    # render only if there is data available
    req(playoffdata())
    
    # reactives are only callable inside an reactive context like render
    data <- playoffdata() %>% 
      data.frame()
  })
  
}