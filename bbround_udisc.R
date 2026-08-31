# Birdbrain 2026 function script
# C GeoffStats LLC 2026
# function: bbround_udisc()
# input: 
  # round number - might be beneficial to eventually make this based on time of processing, since ideally you'd do it right after?
  # link - the link to the downloaded UDisc data with the final playoff results if applicable
  # out - will be the workbook name in the runsheet() script to which this all writes
# output:
  # .txt file with name based on the working directory and the date/course the round was played on. Date comes first to sort properly in output folder.

# Current Input Index
# 1. Entire Season Schedule
# 2. Schedule Information for Today's Round
# 3. Standings Prior to Update (tags removed)
# 4. Pools (for round result - using 2025 classifications!)
# 5. Pool Averages by Layout (needed for WCS slope calculation)
# 6. Handicap Adjustments
# 7. Course Record Check (will see if new layout or existing record, then check what is there against hot round of today)
# 8. All Time Through 2024 by Season
# 9. Current All-Time including 2025 Season
# 10. Tag Sheet (tag value is mutated to 'Previous')

bbround_udisc <- function(round,link,out)
{
 
  input <- bbinput(round,wb=out)
  is_first_league_season <- nrow(as.data.frame(input[9])) == 0
  sink.reset()
  
  colname <- paste0(as.data.frame(input[1])$Course, '_', as.data.frame(input[1])$Date) %>%
    gsub(' ', '_', .)
  
  sink(file = paste0('Rd', round, '_', as.data.frame(input[1])$Date, '_', as.data.frame(input[1])$Course,"_Recap.txt"))
  
  cat(paste0("BIRDBRAIN ROUND RECAP v2.01\nA program by Geoff Chappelle (C) 2026 GEOFFSTATS LLC\n\n\n\n"))
  cat(paste0("This is the recap for round ", round, " of the season\n\n\n"))
  
  if (!is.na(link))
  {
    cat(paste0("Reading today's UDisc file...\n\n"))
    
    today <- readxl::read_xlsx(paste0(link, ".xlsx")) %>%
      filter(position != "DUP" & position != "DNF" & division == 'GEN') %>%
      mutate(Name = trimws(name))
    
    todayscore <- today %>%
      select(Name, 
             "{colname}" := round_total_score)
    newfsc <- merge(as.data.frame(input[13]),todayscore,by=c("Name"),all=T)
    
    completed_rounds <- ncol(newfsc) - 1
    unique_players <- nrow(newfsc)
    
    sham_ready <- completed_rounds >= 11 && unique_players >= 40
    
    write_sheet(newfsc,
                ss=out,
                sheet='Full Season Scores')
    cat(paste0("Scores added to the file to facilitate pool assignments.\n"))
    
    cat(paste0('Adjusting points.\n'))
    ptsdf <- bbpoints(today,as.data.frame(input[3]),as.data.frame(input[9]),as.data.frame(input[10]),as.integer(as.data.frame(input[1])$multiplier[1]),round,wb=out)
  
    
    if(ncol(newfsc) <= 10 && is_first_league_season)
    {
      cat(paste0('Pools will remain unassigned until the round 11 update. Until then, handicap is simply relative to course par. \n'))
      todayadj <- today %>%
        select(Name,
               "{colname}" := round_relative_score)
      newadj <- merge(as.data.frame(input[6]),todayadj,by=c("Name"),all=T) %>%
        select(-Handicap)
      for(i in 1:nrow(newadj)) {
        if(i==1){
          x <- newadj[i,] %>% select(-Name)
        } else {
          x <- newadj[i,] %>% select(-Name,-Handicap)
        }
        x2 <- as.data.frame(t(x))
        colnames(x2) = c('Score')
        x2 <- x2 %>%
          filter(!is.na(Score))
        rounds = as.integer(nrow(x2))
        cut = floor(rounds / 5)
        x3 <- x2 %>%
          arrange(Score) %>%
          mutate(maxrank = row_number(),
                 minrank = rounds - row_number() + 1,
                 cutflag = case_when(cut == 0 ~ 'N',
                                     TRUE ~ case_when(maxrank <= cut | minrank <= cut ~ 'Y',
                                                      TRUE ~ 'N'))) %>%
          filter(cutflag == 'N')
        newhcp = as.numeric(mean(x3$Score))
        newadj$Handicap[i] = newhcp
      }
      write_sheet(newadj,
                  ss=out,
                  sheet='Handicap')
      newadj <- newadj %>%
        rename(Handicapn=Handicap)
      
    } else if(ncol(newfsc) == 11 && is_first_league_season)
    {
      cat(paste0('Next round the league hits the required 40 unique players, the first player pools and slope calculations will be made to adjust the league-wide handicap.\n'))
      todayadj <- today %>%
        select(Name,
               "{colname}" := round_relative_score)
      newadj <- merge(as.data.frame(input[6]),todayadj,by=c("Name"),all=T) %>%
        select(-Handicap)
      for(i in 1:nrow(newadj)) {
        if(i==1){
          x <- newadj[i,] %>% select(-Name)
        } else {
          x <- newadj[i,] %>% select(-Name,-Handicap)
        }
        x2 <- as.data.frame(t(x))
        colnames(x2) = c('Score')
        x2 <- x2 %>%
          filter(!is.na(Score))
        rounds = as.integer(nrow(x2))
        cut = floor(rounds / 5)
        x3 <- x2 %>%
          arrange(Score) %>%
          mutate(maxrank = row_number(),
                 minrank = rounds - row_number() + 1,
                 cutflag = case_when(cut == 0 ~ 'N',
                                     TRUE ~ case_when(maxrank <= cut | minrank <= cut ~ 'Y',
                                                      TRUE ~ 'N'))) %>%
          filter(cutflag == 'N')
        newhcp = as.numeric(mean(x3$Score))
        newadj$Handicap[i] = newhcp
      }
      write_sheet(newadj,
                  ss=out,
                  sheet='Handicap')
      newadj <- newadj %>%
        rename(Handicapn=Handicap)
      
    } else if(is_first_league_season && !sham_ready)
    {
      cat(paste0('11 rounds have been completed, but there are still fewer than 40 unique players. Handicap is relative to course par until more unique players participate.\n'))
      todayadj <- today %>%
        select(Name,
               "{colname}" := round_relative_score)
      newadj <- merge(as.data.frame(input[6]),todayadj,by=c("Name"),all=T) %>%
        select(-Handicap)
      for(i in 1:nrow(newadj)) {
        if(i==1){
          x <- newadj[i,] %>% select(-Name)
        } else {
          x <- newadj[i,] %>% select(-Name,-Handicap)
        }
        x2 <- as.data.frame(t(x))
        colnames(x2) = c('Score')
        x2 <- x2 %>%
          filter(!is.na(Score))
        rounds = as.integer(nrow(x2))
        cut = floor(rounds / 5)
        x3 <- x2 %>%
          arrange(Score) %>%
          mutate(maxrank = row_number(),
                 minrank = rounds - row_number() + 1,
                 cutflag = case_when(cut == 0 ~ 'N',
                                     TRUE ~ case_when(maxrank <= cut | minrank <= cut ~ 'Y',
                                                      TRUE ~ 'N'))) %>%
          filter(cutflag == 'N')
        newhcp = as.numeric(mean(x3$Score))
        newadj$Handicap[i] = newhcp
      }
      write_sheet(newadj,
                  ss=out,
                  sheet='Handicap')
      newadj <- newadj %>%
        rename(Handicapn=Handicap)
      
    } else if(is_first_league_season && sham_ready)
    {
      cat(paste0('The minimum requirements for the slope system to activate have been met.\nAssigning first player pools...\n'))
      newfsc$avg <- rowMeans(newfsc[2:ncol(newfsc)], na.rm = TRUE)
      x <- quantile(rowMeans(newfsc[2:ncol(newfsc)], na.rm = TRUE), probs = c(0.2, 0.4, 0.6, 0.8), na.rm=TRUE)
      
      ppools <- newfsc %>%
        mutate(Pool = case_when(avg <= as.numeric(x[1]) ~ 'A',
                                     avg <= as.numeric(x[2]) ~ 'B',
                                     avg <= as.numeric(x[3]) ~ 'C',
                                     avg <= as.numeric(x[4]) ~ 'D',
                                     !is.na(avg) ~ 'E',
                                     TRUE ~ NA)) %>%
        filter(!is.na(Name))
      ppools$rounds <- rowSums(!is.na(ppools[2:ncol(ppools)-1]))-2
      
      pools <- ppools %>%
        mutate(Pool = ifelse(rounds >= 5, Pool, '')) %>%
        select(Name,Pool)
      
      write_sheet(pools,
                  ss=out,
                  sheet='Player Pool Assignments')
      
      cat(paste0('Done. Now assigning pool averages to first 11 rounds...\n'))
      df <- merge(newfsc,pools,by=c("Name"),all.y=T)
      
      htemp <- df %>%
        select(Name, Pool, Score=2) %>%
        na.omit() %>%
        group_by(Pool) %>%
        summarise(Players = n(),
                  Strokes = sum(Score),
                  Avg = mean(Score),
                  StdDev = sd(Score))
      
      stemp <- as.data.frame(input[12]) %>%
        filter(RoundNo == 1) %>%
        transmute(RdNo=RoundNo, Course, Layout, Par)
      
      poolavg <- as.data.frame(c(stemp,htemp)) %>%
        mutate(ParStrokes = Par*Players)
      
      # now for the rest of the rounds (except for the current one, because bbsham will use that normally now)
      for(i in 3:11)
      {
        htemp <- df %>%
          select(Name, Pool, Score=i) %>%
          na.omit() %>%
          group_by(Pool) %>%
          summarise(Players = n(),
                    Strokes = sum(Score),
                    Avg = mean(Score),
                    StdDev = sd(Score))
        
        stemp <- as.data.frame(input[12]) %>%
          filter(RoundNo == i-1) %>%
          select(Course,Layout,RoundNo,Par)
        
        patemp <- as.data.frame(c(stemp,htemp)) %>%
          mutate(ParStrokes = Par*Players,
                 RdNo = i-1)
        
        poolavg <- merge(x=poolavg,y=patemp, all = TRUE) %>%
          transmute(RdNo,Course,Layout,Par,Pool,Players,Strokes,Avg,StdDev,ParStrokes)
      }
      
      write_sheet(poolavg,
                  ss = out,
                  sheet = 'Poolwise Strokes by Round')
      
      cat(paste0('Done. The input should load with the pools assigned normally and bbsham() will be utilized for slope and handicap.\n
                 Update pools at any time by using bbpools() between rounds.\n\n'))
      slopeadj <- bbsham(today,pools,poolavg,as.data.frame(input[6]),as.data.frame(input[1]),colname,out)
      newslope <- as.data.frame(slopeadj[1])
      newadj <- as.data.frame(slopeadj[2])
      write_sheet(newslope,
                  ss=out,
                  sheet='Course Slopes and Ratings')
    } else
    {
      cat(paste0('Adjusting handicap using SHAM.\n'))
      slopeadj <- bbsham(today,as.data.frame(input[4]),as.data.frame(input[5]),as.data.frame(input[6]),as.data.frame(input[1]),colname,out)
      newslope <- as.data.frame(slopeadj[1])
      newadj <- as.data.frame(slopeadj[2])
      write_sheet(newslope,
                  ss=out,
                  sheet='Course Slopes and Ratings')
    }
    
    round_half_up <- function(x) {
      sign(x) * floor(abs(x) + 0.5)
    }
    cat(paste0('Writing both of those to the new leaderboard.\n'))
    newboard <- merge(ptsdf,newadj,by=(c("Name")),all=T) %>%
      arrange(desc(Points),Rounds) %>%
      transmute(Name,
                Points,
                Rounds,
                Handicap = case_when(Rounds <= 5 ~ case_when(Handicapn > 8 ~ '+8',
                                                            Handicapn > 0.5 ~ paste0('+', round_half_up(Handicapn)),
                                                            Handicapn > -0.5 ~ 'E',
                                                            Handicapn > -5 ~ paste0(round_half_up(Handicapn)),
                                                            TRUE ~ '-5',),
                                     TRUE ~ case_when(Handicapn > 0.5 ~ paste0('+', round_half_up(Handicapn)),
                                                      Handicapn > -0.5 ~ 'E',
                                                      TRUE ~ paste0(round_half_up(Handicapn)))))
    write_sheet(newboard,
                ss=out,
                sheet='Leaderboard')
    
    # check for course record
    hotround <- today %>%
      filter(event_total_score == min(event_total_score))
    
    if(nrow(as.data.frame(input[8])) == 0)
    {
      cat(paste0("There was no course record! Therefore, congratulations to ", hotround$name, " for setting it with a score of ", hotround$event_total_score, " (", hotround$event_relative_score, ")!\n\n"))
      recrow <- cbind(hotround,as.data.frame(input[1])) %>%
        transmute(Course,
                  Layout,
                  Name=name,
                  Score=round_total_score,
                  Date=Datend)
      # have to remove old course record(s)!!
      newrec <- rbind(as.data.frame(input[7]),recrow) %>%
        arrange(desc(Date)) %>%
        group_by(Course,Layout) %>%
        filter(Score == min(Score)) %>%
        ungroup()
      write_sheet(newrec,
                  ss= out,
                  sheet = 'Course Records')
    }
    else
    {
      cat(paste0("The course record was previously held by ", as.data.frame(input[8])$Name[1], " (", as.data.frame(input[8])$Score[1], ")\n"))
      
      if (hotround$event_total_score[1] <= as.data.frame(input[8])$Score[1])
      {
        if (hotround$event_total_score[1] < as.data.frame(input[8])$Score[1])
        {
          cat(paste0("And it was BROKEN today by ", hotround$name, " with a score of ", hotround$event_total_score, " (", hotround$event_relative_score, ")!\n\n"))
        }
        if (hotround$event_total_score[1] == as.data.frame(input[8])$Score[1])
        {
          cat(paste0("And it was tied today by ", hotround$name, " congratulations!\n\n"))
        }
        recrow <- cbind(hotround,as.data.frame(input[1])) %>%
          transmute(Course,
                    Layout,
                    Name=name,
                    Score=round_total_score,
                    Date=Datend)
        # have to remove old course record(s)!!
        newrec <- rbind(as.data.frame(input[7]),recrow) %>%
          arrange(desc(Date)) %>%
          group_by(Course,Layout) %>%
          filter(Score == min(Score)) %>%
          ungroup()
        write_sheet(newrec,
                    ss= out,
                    sheet = 'Course Records')
      }
      else
      {
        cat(paste0("...And the record still stands to this day.\n\n"))
      }
    }
    
    # get ace pot and then check for aces
    nholes <- as.integer(ncol(today))-2
    fives <- strsplit(as.character(as.data.frame(input[1])$ParFives[1]),",") %>%
      as.data.frame() %>%
      rename(fives=1)
    fours <- strsplit(as.character(as.data.frame(input[1])$ParFours[1]),",") %>%
      as.data.frame() %>%
      rename(fours=1)
    holes <- data.frame(average = colMeans(today[,16:nholes]),
                        holes = colnames(today[,16:nholes])) %>%
      transmute(average,
                holen = row_number(),
                hole = sub("hole_", "", holes), # need a workable number instead of row.name()
                par = case_when(hole %in% fives$fives ~ 5,
                                hole %in% fours$fours ~ 4,
                                TRUE ~ 3),
                diff = round(average - par, digits=2)) %>% 
      arrange(desc(diff)) %>%
      mutate(diffc = ifelse(diff > 0, paste0('+', as.character(diff)), as.character(diff)))
    acepot = ifelse(is.na(as.data.frame(input[1])$AcePot[1]),0,as.integer(as.data.frame(input[1])$AcePot[1]))
    newpot = acepot + nrow(today)
    if(any(today[,16:nholes]==1) == FALSE)
    {
      cat("No aces today!\n")
      acepot = newpot
      cat("The ace pot is now $", newpot, ".\n\n")
      
    } else
    {
      # search for ace
      aces <- data.frame(which(today[,16:nholes] == 1, arr.ind=TRUE)) %>%
        rename(playern = row,
               holen = col)
      
      todace <- today %>%
        mutate(playern = row_number())
      
      aceholes <- merge(x=aces,y=holes,by=c('holen'),all.x=TRUE)
      
      acers <- merge(x=aceholes,y=todace,by=c('playern'),all.x=TRUE) %>%
        select(name,hole)
      
      if(nrow(acers) == 1)
      {
        cat("Congrats to ", acers$name[1], " who aced Hole ", acers$hole[1], 
            " and collects the ace pot of $", newpot, "!\n\n")
        acerow <- cbind(acers,as.data.frame(input[1])) %>%
          transmute(Name=name, 
                    Date=Datend, 
                    Course, 
                    Layout, 
                    Hole=hole, 
                    Payout=newpot)
        
      } else if(nrow(acers) > 1)
      {
        cat("There were MULTIPLE aces today! \n")
        for(i in 1:nrow(acers))
        {
          cat(acers$name[i], " - Hole ", acers$hole[i], "\n")
        }
        share = newpot / nrow(acers)
        cat("They will split the pot for $", share, " each!\n\n")
        acerow <- cbind(acers,as.data.frame(input[1])) %>%
          transmute(Name=name, 
                    Date=Datend, 
                    Course, 
                    Layout, 
                    Hole=hole, 
                    Payout=share)
      }
      acepot = 0
      # update in spreadsheet
      newace <- rbind(as.data.frame(input[11]),acerow) %>%
        arrange(desc(Date))
      sheet_write(newace,
                  ss= out,
                  sheet = 'Aces')
      cat(paste0("Recorded in the all-time ace list.\n\n"))
    }
    # update schedule with new ace pot
    acepotschedule <- as.data.frame(input[12])
    if(round != max(as.data.frame(input[12])$RoundNo))
    {
         acepotschedule$AcePot[round+1] = acepot
    sheet_write(acepotschedule,
                ss= out,
                sheet = 'League Schedule')
    }
    else
    {
      cat(paste0('The season ends with a leftover acepot of $', acepot, '!!!'))
    }
    
    cat(paste0("Scoring Summary:\n\n"))
    cat(paste0("Six Toughest Holes: \n"))
    cat(paste0("1. Hole ", holes$hole[1], " (", holes$diffc[1], " average to par)\n"))
    cat(paste0("2. Hole ", holes$hole[2], " (", holes$diffc[2], ")\n"))
    cat(paste0("3. Hole ", holes$hole[3], " (", holes$diffc[3], ")\n"))
    cat(paste0("4. Hole ", holes$hole[4], " (", holes$diffc[4], ")\n"))
    cat(paste0("5. Hole ", holes$hole[5], " (", holes$diffc[5], ")\n"))
    cat(paste0("6. Hole ", holes$hole[6], " (", holes$diffc[6], ")\n\n"))
    
    # now invert
    holes <- holes %>%
      arrange(diff)
    
    cat(paste0("Six Easiest Holes: \n"))
    cat(paste0("1. Hole ", holes$hole[1], " (", holes$diffc[1], " average to par)\n"))
    cat(paste0("2. Hole ", holes$hole[2], " (", holes$diffc[2], ")\n"))
    cat(paste0("3. Hole ", holes$hole[3], " (", holes$diffc[3], ")\n"))
    cat(paste0("4. Hole ", holes$hole[4], " (", holes$diffc[4], ")\n"))
    cat(paste0("5. Hole ", holes$hole[5], " (", holes$diffc[5], ")\n"))
    cat(paste0("6. Hole ", holes$hole[6], " (", holes$diffc[6], ")\n\n"))
    
    
    cat(paste0("This concludes the recap. See you next time for round ",round+1," at ", as.data.frame(input[2])$Course, " - ", as.data.frame(input[2])$Layout,"!"))
  }
  else
  {
    stop("Invalid UDisc link!!")
  }
  sink()
}