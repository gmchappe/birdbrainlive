# Birdbrain 2026 function script
# C GeoffStats LLC 2026
# function: bbpoints()
# input: 
  # df - today's scores
  # lb - current leaderboard
  # atp - all time in all seasons prior to the current one
  # atc - current all time, includes any adjustments made this season
  # multiplier - double points or not
  # rd - if it's the first round, the variables need to be treated slightly differently, because nobody is in the old leaderboard yet.
# output: list of players and new point total

bbpoints <- function(df,lb,atp,atc,multiplier,rd,wb)
{
  require(tidyverse)
  require(googlesheets4)
  
  cat(paste0("Updating points... \n"))
  
  # increment rounds and points from today's round
  # note: behaves slightly differently the first time versus every other time
  if(rd == 1)
  {
    update <- merge(x=df, y=lb, by=c("name"), all = TRUE) %>%
      mutate(name = trimws(name)) %>%
      transmute(name,
                round_total_score,
                position_raw,
                payout = ifelse(is.na(payout),0,payout),
                oldhcp = suppressWarnings(ifelse(is.na(Handicap),0,as.numeric(Handicap))),
                rounds = case_when(is.na(Rounds) ~ 1,
                                   !is.na(Rounds) & is.na(round_total_score) ~ Rounds,
                                   !is.na(Rounds) & !is.na(round_total_score) ~ Rounds + 1,
                                   TRUE ~ 0),
                oldpts = ifelse(is.na(Points),0,Points),
                netscore = round_total_score - oldhcp) %>%
      arrange(desc(payout),netscore) %>%
      mutate(rank = row_number()) %>%
      group_by(netscore, payout) %>%
      mutate(pts = ifelse(payout != 0, (nrow(df)- rank + 1)*multiplier, (nrow(df) - min(rank) + 1)*multiplier)) %>%
      ungroup() %>%
      transmute(Name=name,
             newpts = pts + oldpts,
             newrds = rounds,
             PlayoffFlag = ifelse(oldpts < 300 & newpts >= 300, 'Y', 'N'))
  }
  else
  {
    # separate those who played today
    newresult <- merge(x=df, y=lb, by=c("name"), all.x=TRUE) %>%
      mutate(name = trimws(name)) %>%
      transmute(name,
                round_total_score,
                position_raw,
                payout = ifelse(is.na(payout),0,payout),
                oldhcp = case_when(is.na(Handicap) ~ 0,
                                   Handicap == 'E' ~ 0,
                                   TRUE ~ as.numeric(Handicap)),
                rounds = ifelse(is.na(Rounds),1,Rounds + 1),
                oldpts = ifelse(is.na(Points),0,Points),
                netscore = round_total_score - oldhcp) %>%
      arrange(desc(payout),netscore) %>%
      mutate(rank = row_number()) %>%
      group_by(netscore,payout) %>%
      mutate(pts = ifelse(payout != 0, (nrow(df)- rank + 1)*multiplier, (nrow(df) - min(rank) + 1)*multiplier)) %>%
      ungroup() %>%
      transmute(Name=name,
             newpts = pts + oldpts,
             newrds = rounds,
             PlayoffFlag = ifelse(oldpts < 300 & newpts >= 300, 'Y', 'N'))
    
    # carry over leaderboard among those who did not
    oldlb <- subset(lb, !(name %in% newresult$Name)) %>%
      transmute(Name=name,
                newpts=Points,
                newrds=Rounds,
                PlayoffFlag='N')
    
    update <- rbind(newresult,oldlb)
  }

  # determine new playoff qualifiers and print
  newqs <- update %>%
    filter(PlayoffFlag == 'Y')
  qs <- update %>%
    filter(newpts >= 300)
  
  if(nrow(newqs) != 0) {
    cat("Congrats to New Playoff Qualifiers: \n")
    for(i in 1:nrow(newqs))
      {
        cat(paste0(newqs$Name[i], "\n"))
      }
    cat("That makes ", nrow(qs), " total!\n\n")
  } else {
    cat("No new playoff qualifiers after this round.\n\n")
  }
  
  
  # change board into suitable row of all-time to combine with previous all-time
  points25 <- update %>%
    select(-PlayoffFlag) %>%
    transmute(Name,
              Points=newpts,
              Rounds=newrds,
              Season='2026')
  
  # current all-time, change the milestonen to oldstone to flag a new 500-point increment
  alltime <- atc %>%
    rename(oldstone=milestonen)
  
  newat <- rbind(points25,atp) %>%
    group_by(Name) %>%
    reframe(Name,
            newszn = n(),
            newrounds = sum(Rounds),
            newpts = sum(Points)) %>%
    distinct()
  
  newat2 <- merge(x=newat,y=alltime, by=c("Name"),all=T) %>%
    mutate(hunfl = ifelse(Points < 500 & newpts >= 500, 'Y', 'N'),
           hunfl2 = ifelse((floor(newpts/500)-floor(Points/500)==1) & (floor(newpts/500)%%2 == 1), 'Y', 'N'),
           thoufl = ifelse(floor(newpts/1000)-floor(Points/1000)==1, 'Y', 'N'),
           milestonec = case_when(hunfl == 'Y' ~ paste0(' 500 career points!'),
                                  hunfl2 == 'Y' ~ paste0(floor(newpts/1000), '500 career points!'),
                                  thoufl == 'Y' ~ paste0(floor(newpts/1000), '000 career points!'),
                                  TRUE ~ NA),
           milestonen = as.integer(substr(milestonec,1,4)))
  
  newat3 <- newat2 %>%
    mutate(printfl = case_when(milestonen == oldstone ~ 'N',
                               !is.na(milestonen) ~ 'Y',
                               TRUE ~ 'N')) %>%
    arrange(desc(milestonec),desc(newpts))
  
  cat("All-time points have been updated.\n")
  
  for(i in 1:nrow(newat3))
  {
    if(newat3$printfl[i] == 'Y') {
      cat("Congrats to ", newat3$Name[i], "on", newat3$milestonec[i], "\n")
    }
  }
  # update all-time
  alltime <- newat3 %>%
    transmute(Name,
              Seasons=newszn,
              Rounds=newrounds,
              Points=newpts,
              milestonen) %>%
    arrange(desc(Points))
  
  write_sheet(alltime,
              ss= wb,
              sheet = 'Current All Time')
  
  cat(paste0("Done with points adjusting.\n\n\n"))
  
  newboard <- points25 %>%
    select(-Season)

  return(newboard)
}